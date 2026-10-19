"""Aplicación FastAPI del bot: rutas, `/health`, logs JSON y Sentry sin PII (§6.1, §6.2, §6.3)."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from observabilidad import configurar_logging, iniciar_sentry
from platform_tenancy import crear_pool
from seguridad_http import MiddlewareHeadersSeguridad, MiddlewareLimiteBody

from bot_app.config import (
    SERVICIO,
    base_url_telegram_desde_entorno,
    dsn_desde_entorno,
    dsn_sentry_desde_entorno,
    verificar_rol,
)
from bot_app.consultas import mostrar_menu_principal
from bot_app.despachador import Despachador
from bot_app.flujos import handlers_por_defecto
from bot_app.identidad.calendario import fabrica_calendario_desde_entorno
from bot_app.webhook import router

TIMEOUT_HEALTH_SEG = 3.0
_log = logging.getLogger("bot_app")


def crear_app(
    *,
    pool: Any = None,
    despachador: Despachador | None = None,
    cliente_http: httpx.AsyncClient | None = None,
    fabrica_calendario: Callable[[Any, Any], Any] | None = None,
    base_url_telegram: str | None = None,
) -> FastAPI:
    """Sin `pool`, lo crea al arrancar desde `BOT_DATABASE_URL` (que tiene que ser del rol `bot_service`)."""

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        configurar_logging()
        iniciar_sentry(dsn_sentry_desde_entorno(), SERVICIO)
        propio = app.state.pool is None
        if propio:
            # statement_cache_size=0: compatible con el pooler de Supabase en modo transacción (§4.3).
            app.state.pool = await crear_pool(dsn_desde_entorno(), statement_cache_size=0, servicio="bot")
        try:
            await verificar_rol(app.state.pool)
            yield
        finally:
            if propio:
                await app.state.pool.close()
                app.state.pool = None

    app = FastAPI(title="turnos-bot", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    app.state.despachador = despachador or Despachador(handlers_por_defecto(), por_defecto=mostrar_menu_principal)
    # La configuración de Google se lee y valida acá, una sola vez; las operaciones de Calendar la reutilizan.
    # Sin las variables queda la fábrica deshabilitada (Calendar apagado). En tests se inyecta una simulada.
    app.state.fabrica_calendario = fabrica_calendario or fabrica_calendario_desde_entorno()
    # Igual con la base de la Bot API: se valida acá y el webhook la entrega al adapter y a las acciones posteriores.
    app.state.base_url_telegram = base_url_telegram or base_url_telegram_desde_entorno()
    app.state.cliente_http = cliente_http
    app.include_router(router)
    # Más externo = último agregado: headers de seguridad > límite de body (un update de Telegram pesa pocos KB).
    app.add_middleware(MiddlewareLimiteBody)
    app.add_middleware(MiddlewareHeadersSeguridad)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        """200 solo si hay conexión a la base y es del rol `bot_service`; si no, 503 (UptimeRobot, §6.2)."""
        try:
            await asyncio.wait_for(verificar_rol(request.app.state.pool), timeout=TIMEOUT_HEALTH_SEG)
        except Exception as error:  # noqa: BLE001 - cualquier falla (base caída o congelada, rol equivocado) es "no sano"
            _log.warning("health_fallido: %s", type(error).__name__)
            return JSONResponse({"estado": "no_disponible"}, status_code=503)
        return JSONResponse({"estado": "ok"})

    return app
