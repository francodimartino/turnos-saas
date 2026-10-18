"""Fábrica de la app FastAPI del dashboard-backend (ARCHITECTURE.md §3.1.5, §5, §6).

`crear_app` recibe todo lo externo (config, verificador de JWT, pool, cliente de Supabase Auth) para poder probarla
con piezas simuladas; `dashboard_backend.main:crear_app_desde_entorno` es la fábrica de producción
(`uvicorn dashboard_backend.main:crear_app_desde_entorno --factory`).
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
from platform_auth import ClienteAuthAdmin, VerificadorJWT
from platform_calendar import ConfigGoogle
from platform_tenancy import ERRORES_SERVICIO_OCUPADO, crear_pool, cuerpo_servicio_ocupado
from proteccion_login import VerificadorTurnstile
from seguridad_http import MiddlewareHeadersSeguridad, MiddlewareLimiteBody

from dashboard_backend.calendario import FabricaCalendario, ProveedorCalendario
from dashboard_backend.config import Config
from dashboard_backend.cookies import MiddlewareSesionCookie
from dashboard_backend.dependencias import DependenciasDashboard
from dashboard_backend.rutas_auth import crear_router_auth
from dashboard_backend.rutas_calendar import crear_router_calendar
from dashboard_backend.rutas_catalogo import crear_router_catalogo
from dashboard_backend.rutas_clientes import crear_router_clientes
from dashboard_backend.rutas_disponibilidad import crear_router_disponibilidad
from dashboard_backend.rutas_settings import crear_router_settings
from dashboard_backend.rutas_staff import cliente_admin_desde_entorno, crear_router_staff
from dashboard_backend.rutas_turnos import OpcionesAvisos, crear_router_turnos
from dashboard_backend.supabase_auth import ClienteSupabaseAuth, ErrorSupabaseAuth

SERVICIO = "dashboard-backend"
TIMEOUT_HEALTH_SEG = 3.0
_log = logging.getLogger("dashboard_backend")


class _ContenedorPool:
    """Referencia al pool de `dashboard_service`; se llena al arrancar (o al construir, si se inyecta)."""

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
    pool: Any = None,
    cliente_auth: ClienteSupabaseAuth | None = None,
    cliente_admin: ClienteAuthAdmin | None = None,
    fabrica_calendario: FabricaCalendario | None = None,
    config_google: ConfigGoogle | None = None,
    opciones_avisos: OpcionesAvisos | None = None,
    verificador_turnstile: VerificadorTurnstile | None = None,
    configurar_observabilidad: bool = True,
) -> FastAPI:
    if configurar_observabilidad:
        configurar_logging()
        iniciar_sentry(config.sentry_dsn, SERVICIO)

    contenedor = _ContenedorPool(pool)
    pool_inyectado = pool is not None
    # Calendar: una sola config (validada por quien arma la app), un solo cliente HTTP y una sola caché de tokens por
    # proceso. Una `fabrica_calendario` inyectada (tests) reemplaza al proveedor y no abre nada.
    proveedor = ProveedorCalendario(config_google)
    fabrica = fabrica_calendario or proveedor

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not pool_inyectado:
            contenedor.pool = await crear_pool(
                config.database_url, servicio="dashboard", statement_cache_size=config.statement_cache_size
            )
        await proveedor.abrir()
        await verificador.abrir()  # JWKS precargado y refrescado en segundo plano (no-op con secreto HS256)
        if fabrica_calendario is None and not proveedor.configurado:
            _log.info(
                "Calendar no configurado: sin variables de Google, los endpoints responden calendar_no_configurado"
            )
        try:
            yield
        finally:
            await verificador.cerrar()
            await proveedor.cerrar()
            if not pool_inyectado and contenedor.pool is not None:
                await contenedor.pool.close()
                contenedor.pool = None

    app = FastAPI(
        title="turnos-saas dashboard-backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    cliente = cliente_auth or ClienteSupabaseAuth.desde_config(config)
    deps = DependenciasDashboard(verificador, contenedor.obtener)
    app.state.dependencias = deps
    app.state.config = config
    app.state.calendario = fabrica  # fábrica de servicios de Calendar (la inyectan los routers que la necesitan)

    @app.exception_handler(ErrorSupabaseAuth)
    async def _error_supabase(_request, error: ErrorSupabaseAuth):
        _log.warning("supabase_auth_rechazo", extra={"codigo": error.codigo, "estado": error.status_http})
        return JSONResponse(
            status_code=error.status_http, content={"detail": {"codigo": error.codigo, "mensaje": error.mensaje}}
        )

    @app.exception_handler(RequestValidationError)
    async def _validacion(_request, error: RequestValidationError):
        # Igual que el defecto de FastAPI pero sin `input`/`ctx`: el 422 nunca repite lo recibido (un password, p. ej.).
        errores = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": errores})

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

    app.include_router(crear_router_auth(config, cliente, deps, verificador, verificador_turnstile))
    app.include_router(crear_router_catalogo(deps))
    app.include_router(crear_router_disponibilidad(deps))
    app.include_router(crear_router_settings(deps))
    app.include_router(
        crear_router_staff(deps, contenedor.obtener, lambda: cliente_admin or cliente_admin_desde_entorno())
    )
    app.include_router(crear_router_calendar(deps, fabrica, config.dashboard_origin))
    app.include_router(crear_router_clientes(deps, contenedor.obtener, fabrica))

    app.include_router(crear_router_turnos(deps, contenedor.obtener, fabrica, opciones_avisos))

    # El último en agregarse es el más externo: logs > headers > CORS > límite de body > origen/cookie > rutas.
    app.add_middleware(MiddlewareSesionCookie, origen_permitido=config.dashboard_origin)
    app.add_middleware(MiddlewareLimiteBody)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.dashboard_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(MiddlewareHeadersSeguridad)
    app.add_middleware(_LogDePeticiones)
    return app
