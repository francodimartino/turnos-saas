"""Fábrica de la app FastAPI del web-backend, el servicio público de la web de reservas (ARCHITECTURE.md §2.12, §3).

`crear_app` recibe todo lo externo (config, pool, cliente de Storage, reloj) para poder probarla con piezas
simuladas; `web_backend.main:crear_app_desde_entorno` es la fábrica de producción
(`uvicorn web_backend.main:crear_app_desde_entorno --factory`).

Es lo más expuesto del sistema, así que: ningún endpoint devuelve un 500 con detalle (todo error tiene código y
estado), `/docs`, `/redoc` y `/openapi.json` están apagados, CORS admite un único origen, y el servicio entra a la
base con el rol `web_reservas`, de mínimo privilegio.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from platform_tenancy import crear_pool, cuerpo_servicio_ocupado
from starlette.exceptions import HTTPException as StarletteHTTPException
from turnos_http import cuerpo_error
from turnos_observabilidad import configurar_logging, iniciar_sentry
from turnos_seguridad_http import MiddlewareHeadersSeguridad, MiddlewareLimiteBody

from web_backend.config import Config
from web_backend.entrada import MiddlewareLargoUrl
from web_backend.errores_db import ERRORES_BASE_NO_DISPONIBLE, ERRORES_SERVICIO_OCUPADO
from web_backend.rutas_negocio import router as router_negocio

SERVICIO = "web-backend"
TIMEOUT_HEALTH_SEG = 3.0
CODIGO_BASE_NO_DISPONIBLE = "base_no_disponible"
MENSAJE_BASE_NO_DISPONIBLE = "El servicio no está disponible. Reintentá en unos segundos."
_log = logging.getLogger("web_backend")

# Mensaje genérico por estado para los errores de enrutamiento que FastAPI levanta sin código propio.
_ERRORES_GENERICOS = {
    404: ("no_encontrado", "El recurso no existe."),
    405: ("metodo_no_permitido", "Este recurso no admite ese método."),
}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017 - datetime.UTC no existe en Python 3.10


class _ContenedorPool:
    """Referencia al pool de `web_reservas`; se llena al arrancar (o al construir, si se inyecta)."""

    def __init__(self, pool: Any = None):
        self.pool = pool


class _LogDePeticiones:
    """ASGI puro: un log JSON por request (método, ruta, estado, duración). Sin query, cookies, headers ni cuerpos."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.monotonic()
        estado = 500

        async def _send(mensaje):
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
            await send(mensaje)

        try:
            await self.app(scope, receive, _send)
        finally:
            _log.info(
                "peticion",
                extra={
                    "metodo": scope["method"],
                    "ruta": scope["path"],
                    "estado": estado,
                    "duracion_ms": round((time.monotonic() - inicio) * 1000, 1),
                },
            )


async def _ping(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.fetchval("select 1")


def crear_app(
    config: Config,
    *,
    pool: Any = None,
    almacenamiento: Any = None,
    reloj: Callable[[], datetime] = _ahora,
    configurar_observabilidad: bool = True,
) -> FastAPI:
    if configurar_observabilidad:
        configurar_logging()
        iniciar_sentry(config.sentry_dsn, SERVICIO)

    contenedor = _ContenedorPool(pool)
    pool_inyectado = pool is not None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not pool_inyectado:
            contenedor.pool = await crear_pool(
                config.database_url, servicio="web", statement_cache_size=config.statement_cache_size
            )
        try:
            yield
        finally:
            if almacenamiento is not None:
                await almacenamiento.cerrar()
            if not pool_inyectado and contenedor.pool is not None:
                await contenedor.pool.close()
                contenedor.pool = None

    app = FastAPI(title="turnos-saas web-backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.pool = contenedor
    app.state.almacenamiento = almacenamiento
    app.state.reloj = reloj

    @app.exception_handler(RequestValidationError)
    async def _validacion(_request: Request, error: RequestValidationError):
        # Solo los nombres de los parámetros con problema: el error de pydantic suele citar el valor recibido.
        campos: list[str] = []
        for e in error.errors():
            nombre = str(e["loc"][-1]) if e["loc"] else "pedido"
            if nombre not in campos:
                campos.append(nombre)
        return JSONResponse(
            status_code=422,
            content=cuerpo_error("parametros_invalidos", "Los parámetros del pedido no son válidos.", campos=campos),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, error: StarletteHTTPException):
        detalle = error.detail
        if isinstance(detalle, dict) and "codigo" in detalle:
            return JSONResponse(status_code=error.status_code, content={"detail": detalle}, headers=error.headers)
        codigo, mensaje = _ERRORES_GENERICOS.get(
            error.status_code, ("solicitud_invalida", "La solicitud no es válida.")
        )
        return JSONResponse(status_code=error.status_code, content=cuerpo_error(codigo, mensaje), headers=error.headers)

    async def _servicio_ocupado(_request: Request, error: BaseException):
        # Pool agotado o query colgada: 503 con código estable y sin traceback por cada request.
        _log.warning("servicio_ocupado", extra={"error_tipo": type(error).__name__})
        return JSONResponse(status_code=503, content=cuerpo_servicio_ocupado(), headers={"Retry-After": "5"})

    async def _base_no_disponible(_request: Request, error: BaseException):
        # Base caída, conexión cortada o rol sin permiso: el cliente recibe un código estable, nunca el detalle.
        _log.warning(
            "base_no_disponible",
            extra={"error_tipo": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)},
        )
        return JSONResponse(
            status_code=503,
            content=cuerpo_error(CODIGO_BASE_NO_DISPONIBLE, MENSAJE_BASE_NO_DISPONIBLE),
            headers={"Retry-After": "5"},
        )

    for clase_error in ERRORES_BASE_NO_DISPONIBLE:
        app.add_exception_handler(clase_error, _base_no_disponible)
    # Los vencimientos del pool son más específicos que `OSError` (el `TimeoutError` lo es): Starlette elige el
    # manejador de la clase exacta antes que el de sus bases.
    for clase_error in ERRORES_SERVICIO_OCUPADO:
        app.add_exception_handler(clase_error, _servicio_ocupado)

    @app.get("/health")
    async def health():
        """200 solo si el servicio responde y Postgres contesta; 503 sin detalles internos en cualquier otro caso."""
        try:
            if contenedor.pool is None:
                raise RuntimeError("pool sin inicializar")
            await asyncio.wait_for(_ping(contenedor.pool), timeout=TIMEOUT_HEALTH_SEG)
        except Exception as error:
            # WARNING conciso y sin traceback: con la base caída sería un error por cada sondeo del balanceador.
            _log.warning("health_fallido", extra={"error_tipo": type(error).__name__})
            return JSONResponse(status_code=503, content={"estado": "no_disponible"})
        return {"estado": "ok"}

    app.include_router(router_negocio)

    # El último en agregarse es el más externo: logs > headers > CORS > límite de body > URL > rutas.
    app.add_middleware(MiddlewareLargoUrl)
    app.add_middleware(MiddlewareLimiteBody, limite_bytes=config.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.web_origin],
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(MiddlewareHeadersSeguridad)
    app.add_middleware(_LogDePeticiones)
    return app
