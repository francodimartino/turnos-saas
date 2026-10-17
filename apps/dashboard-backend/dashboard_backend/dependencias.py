"""Dependencias de FastAPI que combinan platform_auth + platform_tenancy (ARCHITECTURE.md §3.1.3, §3.1.5).

Cada endpoint pide una `Transaccion`: una transacción de `dashboard_service` ya con `app.current_tenant_id` y
`app.current_actor_id` seteados **desde el JWT verificado y el estado vivo de la base**, nunca desde el body, el
query ni los headers. Variantes:

  - `deps.sesion`                    autentica (JWT + MFA + tenant activo + staff activo, todo en vivo).
  - `deps.sesion_enrollment`         igual, pero acepta `aal1` de owner/admin (solo para el enrollment de MFA).
  - `deps.tx`                        sesión + transacción con tenant y actor.
  - `deps.tx_con_permiso(clave)`     además exige el `permission_key` (el owner vivo siempre lo tiene); el chequeo
                                     corre dentro de la misma transacción que usará el endpoint.
  - `deps.tx_solo_owner`             exige owner vivo (escalación de privilegios, §2.9).

La transacción hace COMMIT al terminar el endpoint y ROLLBACK si este lanza cualquier excepción (incluido
`HTTPException`). Dentro del endpoint los logs llevan `tenant_id` y `actor_type`.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from observabilidad import contexto_log
from platform_auth import (
    ErrorAuth,
    SesionDashboard,
    VerificadorJWT,
    dependencia_dashboard,
    verificar_owner_vivo,
    verificar_permiso,
)
from platform_tenancy import tenant_tx

ACTOR_TYPE = "tenant_user"


@dataclass(frozen=True)
class Transaccion:
    """Lo que recibe un endpoint: la conexión (dentro de la transacción abierta) y la sesión que la originó."""

    conn: Any
    sesion: SesionDashboard


def http_desde_error_auth(error: ErrorAuth) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return HTTPException(
        status_code=error.status_code, detail={"codigo": error.codigo, "mensaje": error.mensaje}, headers=headers
    )


class DependenciasDashboard:
    def __init__(self, verificador: VerificadorJWT, obtener_pool: Callable[[], Any]):
        self._obtener_pool = obtener_pool
        self.obtener_pool = obtener_pool
        normal = dependencia_dashboard(verificador, obtener_pool)
        enrollment = dependencia_dashboard(verificador, obtener_pool, enrollment=True)

        async def sesion(request: Request) -> AsyncIterator[SesionDashboard]:
            s = await normal(request)
            with contexto_log(tenant_id=str(s.tenant_id), actor_type=ACTOR_TYPE):
                yield s

        async def sesion_enrollment(request: Request) -> AsyncIterator[SesionDashboard]:
            s = await enrollment(request)
            with contexto_log(tenant_id=str(s.tenant_id), actor_type=ACTOR_TYPE):
                yield s

        self.sesion = sesion
        self.sesion_enrollment = sesion_enrollment

        async def tx(s: Annotated[SesionDashboard, Depends(sesion)]) -> AsyncIterator[Transaccion]:
            async with tenant_tx(obtener_pool(), s.tenant_id, s.user_id) as conn:
                yield Transaccion(conn, s)

        async def tx_solo_owner(s: Annotated[SesionDashboard, Depends(sesion)]) -> AsyncIterator[Transaccion]:
            async with tenant_tx(obtener_pool(), s.tenant_id, s.user_id) as conn:
                try:
                    await verificar_owner_vivo(conn, s)
                except ErrorAuth as e:
                    raise http_desde_error_auth(e) from None
                yield Transaccion(conn, s)

        self.tx = tx
        self.tx_solo_owner = tx_solo_owner

    def tx_con_permiso(self, clave: str) -> Callable[..., AsyncIterator[Transaccion]]:
        if not isinstance(clave, str) or not clave.strip():
            raise ValueError("la clave de permiso debe ser un texto no vacío")
        obtener_pool = self._obtener_pool

        async def _tx(s: Annotated[SesionDashboard, Depends(self.sesion)]) -> AsyncIterator[Transaccion]:
            async with tenant_tx(obtener_pool(), s.tenant_id, s.user_id) as conn:
                try:
                    await verificar_permiso(conn, s, clave)
                except ErrorAuth as e:
                    raise http_desde_error_auth(e) from None
                yield Transaccion(conn, s)

        return _tx
