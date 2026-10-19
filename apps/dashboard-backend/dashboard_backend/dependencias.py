"""Dependencias de FastAPI que combinan platform_auth + platform_tenancy (ARCHITECTURE.md §3.1.3, §3.1.5).

Cada endpoint pide una `Transaccion`: una transacción de `dashboard_service` ya con `app.current_tenant_id` y
`app.current_actor_id` seteados **desde el JWT verificado y el estado vivo de la base**, nunca desde el body, el
query ni los headers. Variantes:

  - `deps.sesion`                    autentica (JWT + MFA + tenant activo + staff activo, todo en vivo).
  - `deps.sesion_enrollment`         igual, pero acepta `aal1` de owner/admin (solo para el enrollment de MFA).
  - `deps.tx`                        UNA transacción con tenant y actor: dentro de ella se lee el estado vivo de la
                                     sesión (tenant, staff, owner); el token inválido es 401 sin tomar conexión.
  - `deps.tx_con_permiso(clave)`     además exige el `permission_key` (el owner vivo siempre lo tiene); el chequeo
                                     corre dentro de la misma transacción que usará el endpoint.
  - `deps.tx_solo_owner`             exige owner vivo (escalación de privilegios, §2.9).

La transacción hace COMMIT al terminar el endpoint y ROLLBACK si este lanza cualquier excepción (incluido
`HTTPException`). Dentro del endpoint los logs llevan `tenant_id` y `actor_type`.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from observabilidad import contexto_log
from platform_auth import (
    ErrorAuth,
    IdentidadJWT,
    SesionDashboard,
    VerificadorJWT,
    dependencia_dashboard,
    dependencia_identidad_dashboard,
    sesion_dashboard_en_vivo,
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

        # Las `tx*` NO dependen de `sesion`: una sola conexión y una sola transacción por request. El token se valida
        # antes de tomar la conexión (401 sin acquire) y el estado vivo (tenant, staff, owner, permiso) se lee dentro
        # de la transacción que usa el endpoint, ya con tenant y actor fijados.
        identidad_dep = dependencia_identidad_dashboard(verificador)

        @asynccontextmanager
        async def _abrir(identidad: IdentidadJWT, verificaciones) -> AsyncIterator[Transaccion]:
            async with tenant_tx(obtener_pool(), identidad.tenant_id, identidad.user_id) as conn:
                try:
                    s = await sesion_dashboard_en_vivo(conn, identidad)
                    await verificaciones(conn, s)
                except ErrorAuth as e:
                    raise http_desde_error_auth(e) from None
                with contexto_log(tenant_id=str(s.tenant_id), actor_type=ACTOR_TYPE):
                    yield Transaccion(conn, s)

        async def _sin_extra(conn: Any, s: SesionDashboard) -> None:
            return None

        async def tx(identidad: Annotated[IdentidadJWT, Depends(identidad_dep)]) -> AsyncIterator[Transaccion]:
            async with _abrir(identidad, _sin_extra) as t:
                yield t

        async def tx_solo_owner(
            identidad: Annotated[IdentidadJWT, Depends(identidad_dep)],
        ) -> AsyncIterator[Transaccion]:
            async with _abrir(identidad, verificar_owner_vivo) as t:
                yield t

        self._identidad_dep = identidad_dep
        self._abrir = _abrir
        # Para los endpoints que abren sus propias transacciones cortas (no retienen la conexión mientras esperan a
        # una API externa): la dependencia que solo valida el token y la apertura de una transacción con sesión viva.
        self.identidad = identidad_dep

        def abrir_sesion(identidad: IdentidadJWT) -> Any:
            return _abrir(identidad, _sin_extra)

        self.abrir_sesion = abrir_sesion
        self.tx = tx
        self.tx_solo_owner = tx_solo_owner

    def abrir_con_permiso(self, identidad: IdentidadJWT, clave: str) -> Any:
        """Transacción con sesión viva que además exige el permiso `clave` (el owner vivo siempre lo tiene)."""

        async def permiso(conn: Any, s: SesionDashboard) -> None:
            await verificar_permiso(conn, s, clave)

        return self._abrir(identidad, permiso)

    def tx_con_permiso(self, clave: str) -> Callable[..., AsyncIterator[Transaccion]]:
        if not isinstance(clave, str) or not clave.strip():
            raise ValueError("la clave de permiso debe ser un texto no vacío")

        async def _tx(identidad: Annotated[IdentidadJWT, Depends(self._identidad_dep)]) -> AsyncIterator[Transaccion]:
            async with self.abrir_con_permiso(identidad, clave) as t:
                yield t

        return _tx


# --- Dependencias de módulo -----------------------------------------------------------------------------------
# Las toman los routers de módulo (`router = APIRouter()` a nivel de módulo): en vez de recibir `deps` por parámetro
# desde una fábrica, resuelven en cada request la `DependenciasDashboard` que `crear_app` dejó en `app.state`. Una
# prueba puede reemplazar `app.state.dependencias` o cada una de estas funciones en `app.dependency_overrides`.


def dependencias_de(request: Request) -> DependenciasDashboard:
    return request.app.state.dependencias


async def identidad_actual(request: Request) -> IdentidadJWT:
    """Valida el token (cookie o Bearer) sin tocar la base; el 401 sale antes de tomar una conexión."""
    return await dependencias_de(request).identidad(request)


async def transaccion(
    request: Request, identidad: Annotated[IdentidadJWT, Depends(identidad_actual)]
) -> AsyncIterator[Transaccion]:
    """Equivalente de `deps.tx`: una transacción con tenant y actor, para cualquier staff activo."""
    async with dependencias_de(request).abrir_sesion(identidad) as t:
        yield t


def transaccion_con_permiso(clave: str) -> Callable[..., AsyncIterator[Transaccion]]:
    """Equivalente de `deps.tx_con_permiso(clave)`. Se llama una vez por permiso, a nivel de módulo."""
    if not isinstance(clave, str) or not clave.strip():
        raise ValueError("la clave de permiso debe ser un texto no vacío")

    async def _tx(
        request: Request, identidad: Annotated[IdentidadJWT, Depends(identidad_actual)]
    ) -> AsyncIterator[Transaccion]:
        async with dependencias_de(request).abrir_con_permiso(identidad, clave) as t:
            yield t

    return _tx
