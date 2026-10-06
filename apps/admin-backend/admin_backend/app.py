"""Fábrica de la app FastAPI del admin-backend (ARCHITECTURE.md §2.11, §3.1.5, §3.6, §5.3, §6).

`crear_app` recibe todo lo externo (config, verificador de JWT, pool, cliente de Supabase Auth y cliente de la Auth
Admin API) para poder probarla con piezas simuladas; `admin_backend.main:crear_app_desde_entorno` es la fábrica de
producción (`uvicorn admin_backend.main:crear_app_desde_entorno --factory`).

Deploy y credenciales separados del dashboard-backend: el pool es del rol `platform_admin` y la `service_role`
solo existe dentro del cliente de la Auth Admin API (invitaciones).
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from observabilidad import configurar_logging, iniciar_sentry
from platform_admin import ClienteAuthAdmin, ErrorDominioAdmin
from platform_auth import VerificadorJWT
from platform_tenancy import crear_pool
from supabase_proxy import ClienteSupabaseAuth, ErrorSupabaseAuth

from admin_backend.config import Config
from admin_backend.cookies import MiddlewareSesionCookie
from admin_backend.dependencias import DependenciasAdmin
from admin_backend.errores import http_desde_error_dominio
from admin_backend.rutas_auth import crear_router_auth
from admin_backend.rutas_panel import crear_router_panel

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
    cliente_auth: ClienteSupabaseAuth | None = None,
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
                config.database_url, min_size=1, max_size=5, statement_cache_size=config.statement_cache_size
            )
        try:
            yield
        finally:
            if not pool_inyectado and contenedor.pool is not None:
                await contenedor.pool.close()
                contenedor.pool = None

    app = FastAPI(title="turnos-saas admin-backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    cliente = cliente_auth or ClienteSupabaseAuth.desde_config(config)
    deps = DependenciasAdmin(verificador, contenedor.obtener)
    app.state.dependencias = deps
    app.state.config = config

    @app.exception_handler(ErrorSupabaseAuth)
    async def _error_supabase(_request, error: ErrorSupabaseAuth):
        _log.warning("supabase_auth_rechazo", extra={"codigo": error.codigo, "estado": error.status_http})
        return JSONResponse(
            status_code=error.status_http, content={"detail": {"codigo": error.codigo, "mensaje": error.mensaje}}
        )

    @app.exception_handler(ErrorDominioAdmin)
    async def _error_dominio(_request, error: ErrorDominioAdmin):
        http = http_desde_error_dominio(error)
        return JSONResponse(status_code=http.status_code, content={"detail": http.detail})

    @app.get("/health")
    async def health():
        """200 solo si el servicio responde y Postgres contesta; 503 sin detalles internos en cualquier otro caso."""
        try:
            await asyncio.wait_for(_ping(contenedor.obtener()), timeout=TIMEOUT_HEALTH_SEG)
        except Exception:
            _log.exception("health_fallido")
            return JSONResponse(status_code=503, content={"estado": "no_disponible"})
        return {"estado": "ok"}

    app.include_router(crear_router_auth(config, cliente, deps, verificador))
    app.include_router(crear_router_panel(deps, contenedor.obtener, cliente_auth_admin))

    # El último en agregarse es el más externo: logs > CORS > origen/cookie > rutas.
    app.add_middleware(MiddlewareSesionCookie, origen_permitido=config.admin_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.admin_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(_LogDePeticiones)
    return app
