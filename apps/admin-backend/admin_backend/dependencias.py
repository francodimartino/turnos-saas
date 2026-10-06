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
from observabilidad import contexto_log
from platform_auth import SesionAdmin, VerificadorJWT, dependencia_admin

ACTOR_TYPE = "platform_admin"


class DependenciasAdmin:
    def __init__(self, verificador: VerificadorJWT, obtener_pool: Callable[[], Any]):
        normal = dependencia_admin(verificador, obtener_pool)
        enrollment = dependencia_admin(verificador, obtener_pool, enrollment=True)

        async def sesion(request: Request) -> AsyncIterator[SesionAdmin]:
            s = await normal(request)
            with contexto_log(actor_type=ACTOR_TYPE):
                yield s

        async def sesion_enrollment(request: Request) -> AsyncIterator[SesionAdmin]:
            s = await enrollment(request)
            with contexto_log(actor_type=ACTOR_TYPE):
                yield s

        self.sesion = sesion
        self.sesion_enrollment = sesion_enrollment
