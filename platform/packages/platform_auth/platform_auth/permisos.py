"""Chequeo de permisos granulares (§2.4, §3.1.5) y de escalación de privilegios (§2.9).

Ninguno de los dos se apoya en el JWT: `is_owner`, `is_active` y los `permission_key` se leen EN VIVO de la base en
cada request, así que revocar un permiso, desactivar al staff o degradar a un owner corta el acceso en el siguiente
request, no cuando vence el token. El owner (`is_owner` en vivo) pasa siempre el chequeo de `permission_key`.

`requiere_owner_vivo` es un caso aparte, no un permiso más de la lista: `manage_staff` NO alcanza para setear o
quitar `is_owner` ni para modificar los permisos de otro (si alcanzara, un empleado con `manage_staff` podría
auto-promoverse). Un endpoint que haga esas operaciones lo usa como dependencia, o llama a `verificar_owner_vivo`
con la conexión de su propia transacción para que el chequeo y la escritura sean atómicos.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from platform_tenancy import tenant_tx

from platform_auth.catalogo_permisos import Permiso
from platform_auth.dependencias import _http
from platform_auth.errores import ErrorAuth, NoEsOwner, PermisoDenegado, StaffInactivo
from platform_auth.sesion import SesionDashboard

# Una sola consulta con el estado vivo del staff en ESTE tenant (la conexión ya trae el contexto de RLS).
# `$3` es la clave de permiso; con NULL el `exists` da false (chequeo de owner sin permiso).
_SQL_ESTADO_STAFF = """
select u.is_owner, u.is_active,
  exists (
    select 1 from public.tenant_user_permissions p
    where p.tenant_user_id = u.id and p.permission_key = $3
  ) as tiene
from public.tenant_users u
where u.tenant_id = $1 and u.auth_user_id = $2
"""

_SQL_CATALOGO = "select key as permission_key from public.permissions order by key"
_SQL_PERMISOS_STAFF = """
select p.permission_key
from public.tenant_user_permissions p
join public.tenant_users u on u.id = p.tenant_user_id
where u.tenant_id = $1 and u.auth_user_id = $2
order by p.permission_key
"""


async def _estado_vivo(conn: Any, sesion: SesionDashboard, clave: Permiso | None) -> Any:
    fila = await conn.fetchrow(_SQL_ESTADO_STAFF, sesion.tenant_id, sesion.user_id, clave)
    if fila is None or fila["is_active"] is not True:
        # Sin fila de staff o desactivado: el acceso cae en el acto.
        raise StaffInactivo()
    return fila


async def verificar_permiso(conn: Any, sesion: SesionDashboard, clave: Permiso) -> None:
    """Lanza `PermisoDenegado` si el staff no tiene `clave` (el owner vivo siempre la tiene)."""
    fila = await _estado_vivo(conn, sesion, clave)
    if fila["is_owner"] is True:
        return
    if fila["tiene"] is not True:
        raise PermisoDenegado()


async def listar_permisos_vivos(conn: Any, sesion: SesionDashboard) -> list[str]:
    """Los `permission_key` vigentes del staff, leídos de la base ahora (nunca del JWT).

    El owner vivo tiene todo el catálogo (coherente con `verificar_permiso`); el resto, solo lo asignado.
    Lanza `StaffInactivo` si el staff no existe o está desactivado.
    """
    fila = await _estado_vivo(conn, sesion, None)
    if fila["is_owner"] is True:
        filas = await conn.fetch(_SQL_CATALOGO)
    else:
        filas = await conn.fetch(_SQL_PERMISOS_STAFF, sesion.tenant_id, sesion.user_id)
    return [f["permission_key"] for f in filas]


async def verificar_owner_vivo(conn: Any, sesion: SesionDashboard) -> None:
    """Lanza `NoEsOwner` si en la base el staff no es owner activo, diga lo que diga el JWT o la sesión."""
    fila = await _estado_vivo(conn, sesion, None)
    if fila["is_owner"] is not True:
        raise NoEsOwner()


def _clave_valida(clave: Any) -> Permiso:
    """Convierte a `Permiso`; una clave fuera del catálogo (o vacía) es un error de programación: `ValueError`."""
    return Permiso(clave)


def requiere_permiso(
    clave: Permiso,
    *,
    dependencia_sesion: Callable[[Request], Awaitable[SesionDashboard]],
    obtener_pool: Callable[[], Any],
) -> Callable[[Request], Awaitable[SesionDashboard]]:
    """Dependencia de FastAPI: autentica (con `dependencia_sesion`) y exige el permiso `clave` en vivo.

    `dependencia_sesion` es la de `dependencia_dashboard(...)`; `obtener_pool` devuelve el pool de
    `dashboard_service`. Devuelve la misma `SesionDashboard`. Sin permiso: 403.
    """
    clave = _clave_valida(clave)

    async def _dependencia(request: Request) -> SesionDashboard:
        sesion = await dependencia_sesion(request)
        try:
            async with tenant_tx(obtener_pool(), sesion.tenant_id, sesion.user_id) as conn:
                await verificar_permiso(conn, sesion, clave)
        except ErrorAuth as e:
            raise _http(e) from None
        return sesion

    return _dependencia


def requiere_owner_vivo(
    *,
    dependencia_sesion: Callable[[Request], Awaitable[SesionDashboard]],
    obtener_pool: Callable[[], Any],
) -> Callable[[Request], Awaitable[SesionDashboard]]:
    """Dependencia de FastAPI: exige que el usuario sea owner activo según la base (§2.9). Sin serlo: 403."""

    async def _dependencia(request: Request) -> SesionDashboard:
        sesion = await dependencia_sesion(request)
        try:
            async with tenant_tx(obtener_pool(), sesion.tenant_id, sesion.user_id) as conn:
                await verificar_owner_vivo(conn, sesion)
        except ErrorAuth as e:
            raise _http(e) from None
        return sesion

    return _dependencia
