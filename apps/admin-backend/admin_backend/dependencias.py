"""Dependencias de FastAPI sobre `platform_auth.dependencia_admin` (ARCHITECTURE.md §3.1.5, §3.6).

El gate de todo el panel: JWT válido + claim `is_platform_admin` + `aal2` + confirmación EN VIVO contra
`platform_admins` (quitar a un admin corta su sesión de inmediato). El claim solo habilita; el acceso a los datos
lo da el rol Postgres `platform_admin` del pool.

  - `deps.sesion`             exige `aal2`.
  - `deps.sesion_enrollment`  igual, pero acepta `aal1` (solo para enrolar el segundo factor, §3.6).

Los logs de un request autenticado llevan `actor_type=platform_admin` (sin `tenant_id`: no hay uno único).
"""

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Request
from platform_auth import SesionAdmin, VerificadorJWT, dependencia_admin
from turnos_sesion import DependenciasSesion, sesion_de_app
from turnos_sesion import dependencias_de as _dependencias_de

ACTOR_TYPE = "platform_admin"


class DependenciasAdmin(DependenciasSesion):
    def __init__(self, verificador: VerificadorJWT, obtener_pool: Callable[[], Any]):
        super().__init__(
            dependencia_admin(verificador, obtener_pool),
            dependencia_admin(verificador, obtener_pool, enrollment=True),
            actor_type=ACTOR_TYPE,
        )


# --- Dependencias de módulo -----------------------------------------------------------------------------------
# Las toman los routers de módulo (`router = APIRouter()` a nivel de módulo): en vez de recibir `deps` y los pools por
# parámetro desde una fábrica, resuelven en cada request lo que `crear_app` dejó en `app.state` (`dependencias`,
# `obtener_pool`, `obtener_pool_readonly`, `cliente_auth_admin`). Una prueba puede reemplazar ese estado o cada una de
# estas funciones en `app.dependency_overrides`.


def dependencias_de(request: Request) -> DependenciasAdmin:
    return _dependencias_de(request)


async def sesion_admin(request: Request) -> AsyncIterator[SesionAdmin]:
    """Equivalente de `deps.sesion`: platform admin con `aal2` confirmado en vivo."""
    async for s in sesion_de_app(request):
        yield s


def pool_admin(request: Request) -> Callable[[], Any]:
    """Getter del pool de `platform_admin` (el pool se obtiene recién al usarlo, ya autenticado)."""
    return request.app.state.obtener_pool


def pool_readonly(request: Request) -> Callable[[], Any]:
    """Getter del pool de `platform_admin_readonly` (solo impersonación)."""
    return request.app.state.obtener_pool_readonly


def cliente_auth_admin_de(request: Request) -> Any:
    return request.app.state.cliente_auth_admin
