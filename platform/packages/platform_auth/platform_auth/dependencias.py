"""Dependencias de FastAPI sobre `autenticar_dashboard` / `autenticar_admin`.

El token se toma del header `Authorization: Bearer <jwt>`. Hoy lo envía el frontend tras el login por el
proxy de Supabase Auth del backend (el navegador nunca habla con Supabase).
"""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from platform_auth.errores import ErrorAuth, NoAutenticado
from platform_auth.identidad import IdentidadJWT
from platform_auth.sesion import (
    SesionAdmin,
    SesionDashboard,
    autenticar_admin,
    autenticar_dashboard,
    identificar_dashboard,
)
from platform_auth.verificador import VerificadorJWT


def extraer_bearer(autorizacion: str | None) -> str:
    """Devuelve el token de un header `Authorization: Bearer <token>` o lanza `NoAutenticado`."""
    if not autorizacion:
        raise NoAutenticado()
    partes = autorizacion.split(" ")
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1]:
        raise NoAutenticado()
    return partes[1]


def _http(error: ErrorAuth) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return HTTPException(
        status_code=error.status_code, detail={"codigo": error.codigo, "mensaje": error.mensaje}, headers=headers
    )


def dependencia_dashboard(
    verificador: VerificadorJWT, obtener_pool: Callable[[], Any], *, enrollment: bool = False
) -> Callable[[Request], Any]:
    """Crea la dependencia `Depends(...)` del dashboard. `obtener_pool` devuelve el pool de `dashboard_service`."""

    async def _dependencia(request: Request) -> SesionDashboard:
        try:
            token = extraer_bearer(request.headers.get("authorization"))
            return await autenticar_dashboard(
                token, verificador=verificador, pool=obtener_pool(), enrollment=enrollment
            )
        except ErrorAuth as e:
            raise _http(e) from None

    return _dependencia


def dependencia_identidad_dashboard(
    verificador: VerificadorJWT, *, enrollment: bool = False
) -> Callable[[Request], Any]:
    """Dependencia que solo valida el token (JWT, `tenant_id`, MFA por claims): no toca la base ni el pool.

    Para rutas que verifican el estado vivo dentro de la transacción del request (`sesion_dashboard_en_vivo`).
    """

    async def _dependencia(request: Request) -> IdentidadJWT:
        try:
            token = extraer_bearer(request.headers.get("authorization"))
            return identificar_dashboard(token, verificador=verificador, enrollment=enrollment)
        except ErrorAuth as e:
            raise _http(e) from None

    return _dependencia


def dependencia_admin(
    verificador: VerificadorJWT, obtener_pool: Callable[[], Any], *, enrollment: bool = False
) -> Callable[[Request], Any]:
    """Crea la dependencia `Depends(...)` del admin. `obtener_pool` devuelve el pool de `platform_admin`."""

    async def _dependencia(request: Request) -> SesionAdmin:
        try:
            token = extraer_bearer(request.headers.get("authorization"))
            return await autenticar_admin(token, verificador=verificador, pool=obtener_pool(), enrollment=enrollment)
        except ErrorAuth as e:
            raise _http(e) from None

    return _dependencia
