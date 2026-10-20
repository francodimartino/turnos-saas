"""Fábrica de la app FastAPI del admin-backend (ARCHITECTURE.md §2.11, §3.1.5, §3.6, §5.3, §6).

`crear_app` recibe todo lo externo (config, verificador de JWT, pool, cliente de Supabase Auth y cliente de la Auth
Admin API) para poder probarla con piezas simuladas; `admin_backend.main:crear_app_desde_entorno` es la fábrica de
producción (`uvicorn admin_backend.main:crear_app_desde_entorno --factory`).

Deploy y credenciales separados del dashboard-backend: el pool es del rol `platform_admin` y la `service_role`
solo existe dentro del cliente de la Auth Admin API (invitaciones). Un segundo pool, del rol
`platform_admin_readonly` (solo SELECT), existe únicamente para la impersonación de solo lectura.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from observabilidad import configurar_logging, iniciar_sentry
from platform_admin import ClienteAuthAdmin, ErrorDominioAdmin
from platform_auth import VerificadorJWT
from platform_tenancy import ERRORES_SERVICIO_OCUPADO, crear_pool, cuerpo_servicio_ocupado
from proteccion_login import VerificadorTurnstile
from seguridad_http import MiddlewareHeadersSeguridad, MiddlewareLimiteBody
from supabase_proxy import ClienteSupabaseAuth, ErrorSupabaseAuth
from turnos_http import respuesta_error
from turnos_sesion import MiddlewareSesionCookie
from turnos_sesion.rutas_auth import router as router_auth

from admin_backend.config import Config
from admin_backend.dependencias import DependenciasAdmin
from admin_backend.errores import http_desde_error_dominio
from admin_backend.rutas_auth import servicios_auth
from admin_backend.rutas_impersonacion import router as router_impersonacion
from admin_backend.rutas_panel import router as router_panel
from admin_backend.rutas_visores import router as router_visores

SERVICIO = "admin-backend"
TIMEOUT_HEALTH_SEG = 3.0
_log = logging.getLogger("admin_backend")


class _ContenedorPool:
    """Referencia al pool de `platform_admin`; se llena al arrancar (o al construir, si se inyecta)."""

    def __init__(self, pool: Any = None):
        self.pool = pool

    def obtener(self) -> Any:
        if self.pool is None:
            raise RuntimeError("el pool de la base no está inicializado")
        return self.pool


class _LogDePeticiones:
    """ASGI puro: un log JSON por request (método, ruta, estado, duración). Sin cookies, headers ni cuerpos."""

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
    verificador: VerificadorJWT,
    cliente_auth_admin: ClienteAuthAdmin,
    pool: Any = None,
    pool_readonly: Any = None,
    cliente_auth: ClienteSupabaseAuth | None = None,
    verificador_turnstile: VerificadorTurnstile | None = None,
    configurar_observabilidad: bool = True,
) -> FastAPI:
    if configurar_observabilidad:
        configurar_logging()
        iniciar_sentry(config.sentry_dsn, SERVICIO)

    contenedor = _ContenedorPool(pool)
    pool_inyectado = pool is not None
    contenedor_readonly = _ContenedorPool(pool_readonly)
    pool_readonly_inyectado = pool_readonly is not None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not pool_inyectado:
            contenedor.pool = await crear_pool(
                config.database_url, servicio="admin", statement_cache_size=config.statement_cache_size
            )
        if not pool_readonly_inyectado:
            contenedor_readonly.pool = await crear_pool(
                config.readonly_database_url,
                servicio="admin_readonly",
                statement_cache_size=config.statement_cache_size,
            )
        await verificador.abrir()  # JWKS precargado y refrescado en segundo plano (no-op con secreto HS256)
        try:
            yield
        finally:
            await verificador.cerrar()
            if not pool_readonly_inyectado and contenedor_readonly.pool is not None:
                await contenedor_readonly.pool.close()
                contenedor_readonly.pool = None
            if not pool_inyectado and contenedor.pool is not None:
                await contenedor.pool.close()
                contenedor.pool = None

    app = FastAPI(title="turnos-saas admin-backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    cliente = cliente_auth or ClienteSupabaseAuth.desde_config(config)
    deps = DependenciasAdmin(verificador, contenedor.obtener)
    app.state.dependencias = deps
    app.state.auth = servicios_auth(config, cliente, verificador, verificador_turnstile, contenedor.obtener)
    app.state.config = config
    app.state.obtener_pool = contenedor.obtener
    app.state.obtener_pool_readonly = contenedor_readonly.obtener
    app.state.cliente_auth_admin = cliente_auth_admin

    @app.exception_handler(ErrorSupabaseAuth)
    async def _error_supabase(_request, error: ErrorSupabaseAuth):
        _log.warning("supabase_auth_rechazo", extra={"codigo": error.codigo, "estado": error.status_http})
        return respuesta_error(error.status_http, error.codigo, error.mensaje)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_request, error: RequestValidationError):
        # Igual que el defecto de FastAPI pero sin `input`/`ctx`: el 422 nunca repite lo recibido (un secret, p. ej.).
        errores = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": errores})

    @app.exception_handler(ErrorDominioAdmin)
    async def _error_dominio(_request, error: ErrorDominioAdmin):
        http = http_desde_error_dominio(error)
        return JSONResponse(status_code=http.status_code, content={"detail": http.detail})

    async def _servicio_ocupado(_request, error: BaseException):
        # Pool agotado o query colgada: 503 con código estable en vez de 500 (y sin traceback por cada request).
        _log.warning("servicio_ocupado: %s", type(error).__name__)
        return JSONResponse(status_code=503, content=cuerpo_servicio_ocupado(), headers={"Retry-After": "5"})

    for clase_error in ERRORES_SERVICIO_OCUPADO:
        app.add_exception_handler(clase_error, _servicio_ocupado)

    @app.get("/health")
    async def health():
        """200 solo si el servicio responde y Postgres contesta; 503 sin detalles internos en cualquier otro caso."""
        try:
            await asyncio.wait_for(_ping(contenedor.obtener()), timeout=TIMEOUT_HEALTH_SEG)
        except Exception as error:
            # WARNING conciso: sin traceback (con el pool caído serían un ERROR/Sentry por cada sondeo del balanceador).
            _log.warning("health_fallido: %s", type(error).__name__)
            return JSONResponse(status_code=503, content={"estado": "no_disponible"})
        return {"estado": "ok"}

    app.include_router(router_auth)
    app.include_router(router_panel)
    app.include_router(router_visores)
    app.include_router(router_impersonacion)

    # El último en agregarse es el más externo: logs > headers > CORS > límite de body > origen/cookie > rutas.
    app.add_middleware(
        MiddlewareSesionCookie, origen_permitido=config.admin_origin, cookie_acceso=config.cookies_sesion.acceso
    )
    app.add_middleware(MiddlewareLimiteBody)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.admin_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(MiddlewareHeadersSeguridad)
    app.add_middleware(_LogDePeticiones)
    return app
