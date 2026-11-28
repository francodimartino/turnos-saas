"""Aplicación del enviador: el bucle del outbox corre dentro del ciclo de vida de una app FastAPI mínima.

FastAPI/uvicorn aporta lo que el servicio necesita sin código propio: `GET /health` para UptimeRobot (§6.2) y el
manejo de SIGTERM, que dispara el apagado ordenado (`ciclo_de_vida`: se avisa al bucle, se espera a las filas en curso
hasta `ENVIADOR_PLAZO_APAGADO_SEG` y se cierran el pool y los clientes HTTP). Un solo proceso: el reclamo con
SKIP LOCKED y arriendo permite correr más instancias sin que repitan una fila.

Todo lo externo se crea una vez, al arrancar: el pool (rol `enviador`, verificado contra la base), el cliente HTTP de
Telegram (timeout explícito) y el cliente de Resend (timeouts de `ConfigEmail`).
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from platform_notifications import BACKOFF_POR_DEFECTO
from platform_tenancy import crear_pool
from turnos_email import ClienteEmail
from turnos_observabilidad import configurar_logging, iniciar_sentry
from turnos_seguridad_http import MiddlewareHeadersSeguridad

from enviador_app.bucle import Enviador
from enviador_app.config import ROL, SERVICIO, Config
from enviador_app.procesador import ProcesadorOutbox
from enviador_app.reporte import reportar_a_sentry

TIMEOUT_HEALTH_SEG = 3.0
TIMEOUT_TELEGRAM = httpx.Timeout(10.0, connect=5.0)
# Margen sobre el plazo de apagado del bucle (que incluye devolver las filas a `pending`).
_MARGEN_APAGADO_SEG = 10.0
_log = logging.getLogger("enviador_app")


class RolIncorrecto(RuntimeError):
    """El pool no conecta como `enviador`."""


async def verificar_rol(pool: Any) -> None:
    """Comprueba en la base que las conexiones son del rol `enviador` (current_user y session_user)."""
    fila = await pool.fetchrow("select current_user::text as actual, session_user::text as sesion")
    if fila is None or fila["actual"] != ROL or fila["sesion"] != ROL:
        raise RolIncorrecto(f"el pool del enviador no conecta como {ROL}")


def crear_app(
    *,
    config: Config | None = None,
    pool: Any = None,
    cliente_http: httpx.AsyncClient | None = None,
    cliente_email: Any = None,
    reportar: Any = reportar_a_sentry,
) -> FastAPI:
    """Sin `config`, la lee y valida del entorno (un valor inválido impide arrancar). Sin `pool`, lo crea al arrancar
    desde `ENVIADOR_DATABASE_URL`; `cliente_http` y `cliente_email` se inyectan solo en tests."""
    config = config or Config.desde_entorno(os.environ)
    maximo_sin_vuelta = config.arriendo_seg + config.espera_maxima_seg

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        configurar_logging()
        iniciar_sentry(config.sentry_dsn, SERVICIO)
        pool_propio = pool is None
        http_propio, correo_propio = cliente_http is None, cliente_email is None
        app.state.pool = pool or await crear_pool(config.dsn, statement_cache_size=0, servicio="enviador")
        http = cliente_http or httpx.AsyncClient(timeout=TIMEOUT_TELEGRAM)
        correo = cliente_email or ClienteEmail(config.email)
        detener = app.state.detener = asyncio.Event()
        tarea: asyncio.Future | None = None
        try:
            await verificar_rol(app.state.pool)
            procesador = ProcesadorOutbox(
                app.state.pool,
                cliente_http=http,
                cliente_email=correo,
                base_url_telegram=config.base_url_telegram,
                backoff=BACKOFF_POR_DEFECTO,
                esperar=asyncio.sleep,
                max_intentos=config.max_intentos,
                reintento_seg=config.arriendo_seg,
                reportar=reportar,
            )
            app.state.enviador = Enviador(
                app.state.pool,
                procesador,
                lote=config.lote,
                concurrencia=config.concurrencia,
                arriendo_seg=config.arriendo_seg,
                max_intentos=config.max_intentos,
                plazo_fila_seg=config.plazo_fila_seg,
                plazo_apagado_seg=config.plazo_apagado_seg,
                intervalo_seg=config.intervalo_seg,
                espera_maxima_seg=config.espera_maxima_seg,
                reportar=reportar,
            )
            app.state.inicio = time.monotonic()
            tarea = app.state.tarea = asyncio.ensure_future(app.state.enviador.correr(detener))
            yield
        finally:
            detener.set()  # SIGTERM: uvicorn cierra el lifespan y el bucle termina lo que tiene en curso
            if tarea is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(tarea), config.plazo_apagado_seg + _MARGEN_APAGADO_SEG)
                except Exception:  # noqa: BLE001 - el apagado no se queda colgado (incluye el plazo agotado)
                    tarea.cancel()
                    _log.error("apagado_forzado", extra={"motivo": "plazo_agotado"})
            if correo_propio:
                await correo.cerrar()
            if http_propio:
                await http.aclose()
            if pool_propio:
                await app.state.pool.close()
            app.state.pool = None

    app = FastAPI(title=SERVICIO, lifespan=ciclo_de_vida, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    app.add_middleware(MiddlewareHeadersSeguridad)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        """200 si el bucle sigue dando vueltas y hay conexión a la base con el rol `enviador`; si no, 503.

        No expone datos: ni contadores, ni nombres de negocio, ni el motivo (queda en el log)."""
        try:
            tarea = request.app.state.tarea
            ultimo = request.app.state.enviador.ultimo_ciclo or request.app.state.inicio
            if tarea.done():
                raise RuntimeError("el bucle terminó")
            if time.monotonic() - ultimo > maximo_sin_vuelta:
                raise RuntimeError("el bucle no avanza")
            await asyncio.wait_for(verificar_rol(request.app.state.pool), timeout=TIMEOUT_HEALTH_SEG)
        except Exception as error:  # noqa: BLE001 - cualquier falla (bucle caído o colgado, base caída, rol equivocado)
            _log.warning("health_fallido", extra={"motivo": type(error).__name__})
            return JSONResponse({"estado": "no_disponible"}, status_code=503)
        return JSONResponse({"estado": "ok"})

    return app
