"""Dominio del staff del local y sus permisos (ARCHITECTURE.md §2.4, §2.9, §3.2): sin FastAPI.

Cada función recibe la conexión de la transacción que abrió el endpoint (con el tenant y el actor ya fijados) y la
sesión viva. La auditoría se escribe en la misma transacción que el cambio: si falla, el cambio se revierte.

Escalación de privilegios: `manage_staff` NO alcanza para dar permisos ni `is_owner`; esos endpoints piden owner vivo
(lo verifica la dependencia del router, dentro de la transacción de la escritura). Tampoco alcanza para desactivar o
reactivar a un owner: `cambiar_activo` lo exige con `verificar_owner_vivo` (`ErrorAuth` sube al router).

El último owner activo lo protege un trigger de la base; acá su rechazo se traduce a `ErrorUltimoOwner`.

Identidad: nombre y email viven en `auth.users`, que este rol no lee: el listado los pide a
`public.get_tenant_staff_identity` (SECURITY DEFINER; solo responde por el tenant del contexto). El nombre y el email
no van a `audit_log` ni a logs: cada cambio queda con ids y claves de permiso.

Invitar (`invitar`) es la única operación que no cabe en una sola transacción, a propósito: llama a la Auth Admin
API (red, sin límite corto de tiempo) y no debe retener una conexión del pool mientras espera. Por eso `invitar_staff`
valida en una transacción corta, llama a la API sin conexión tomada y escribe el alta, los permisos y la auditoría en
otra; la sesión viva se lee en una tercera, antes de empezar. Cada una es corta y ninguna sobrevive a la llamada.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from asyncpg.exceptions import PostgresError
from platform_audit import registrar
from platform_auth import (
    ClienteAuthAdmin,
    ResultadoInvitacion,
    SesionDashboard,
    invitar_staff,
    verificar_owner_vivo,
)

SQL_LISTAR = """
select u.id, u.is_owner, u.is_active, u.created_at, (u.auth_user_id = $2) as es_yo,
  coalesce(array(
    select p.permission_key from public.tenant_user_permissions p
    where p.tenant_user_id = u.id order by p.permission_key
  ), '{}') as permission_keys
from public.tenant_users u
where u.tenant_id = $1
order by u.created_at, u.id
"""
SQL_IDENTIDAD = "select tenant_user_id, email, nombre from public.get_tenant_staff_identity($1)"
# `for update` serializa dos cambios simultáneos sobre la misma persona.
SQL_OBJETIVO = """
select id, is_owner, is_active from public.tenant_users where tenant_id = $1 and id = $2 for update
"""
SQL_CATALOGO = "select key from public.permissions order by key"
SQL_PERMISOS_DE = "select permission_key from public.tenant_user_permissions where tenant_user_id = $1 order by 1"
SQL_QUITAR_PERMISOS = """
delete from public.tenant_user_permissions where tenant_user_id = $1 and permission_key <> all($2::text[])
"""
SQL_AGREGAR_PERMISOS = """
insert into public.tenant_user_permissions (tenant_user_id, permission_key)
select $1::uuid, unnest($2::text[])
on conflict do nothing
"""
SQL_CAMBIAR_OWNER = """
update public.tenant_users set is_owner = $3 where tenant_id = $1 and id = $2 returning id, is_owner, is_active
"""
SQL_CAMBIAR_ACTIVO = """
update public.tenant_users set is_active = $3 where tenant_id = $1 and id = $2 returning id, is_owner, is_active
"""

__all__ = [
    "ErrorPermisoDesconocido",
    "ErrorStaffInactivo",
    "ErrorStaffInexistente",
    "ErrorUltimoOwner",
    "cambiar_activo",
    "cambiar_owner",
    "cambiar_permisos",
    "invitar",
    "listar_staff",
]


class ErrorStaffInexistente(Exception):
    """La persona del equipo no existe en el tenant (o es de otro)."""


class ErrorPermisoDesconocido(Exception):
    """Se pidieron claves que no están en el catálogo de permisos."""

    def __init__(self, claves: list[str]):
        super().__init__(", ".join(claves))
        self.claves = claves


class ErrorStaffInactivo(Exception):
    """No se le da `is_owner` a alguien desactivado."""


class ErrorUltimoOwner(Exception):
    """El trigger de la base rechazó el cambio: es el último owner activo del local."""


@contextmanager
def _traducir_ultimo_owner() -> Iterator[None]:
    try:
        yield
    except PostgresError as e:
        if getattr(e, "sqlstate", None) == "23514" and "último owner" in str(e):
            raise ErrorUltimoOwner from None
        raise


async def _objetivo(conn: Any, tenant_id: uuid.UUID, staff_id: uuid.UUID) -> Any:
    fila = await conn.fetchrow(SQL_OBJETIVO, tenant_id, staff_id)
    if fila is None:
        raise ErrorStaffInexistente
    return fila


def _item(fila: Any, identidad: Any = None) -> dict[str, Any]:
    return {
        "id": str(fila["id"]),
        "nombre": identidad["nombre"] if identidad is not None else None,
        "email": identidad["email"] if identidad is not None else None,
        "is_owner": fila["is_owner"],
        "is_active": fila["is_active"],
        "es_yo": fila["es_yo"],
        "created_at": fila["created_at"].isoformat(),
        "permission_keys": list(fila["permission_keys"]),
    }


def _estado(fila: Any) -> dict[str, Any]:
    return {"id": str(fila["id"]), "is_owner": fila["is_owner"], "is_active": fila["is_active"]}


async def listar_staff(conn: Any, sesion: SesionDashboard) -> dict[str, Any]:
    filas = await conn.fetch(SQL_LISTAR, sesion.tenant_id, sesion.user_id)
    identidades = {i["tenant_user_id"]: i for i in await conn.fetch(SQL_IDENTIDAD, sesion.tenant_id)}
    return {"staff": [_item(f, identidades.get(f["id"])) for f in filas]}


async def invitar(
    email: str,
    permission_keys: list[str],
    nombre: Any,
    *,
    sesion: SesionDashboard,
    pool: Any,
    cliente: ClienteAuthAdmin,
) -> ResultadoInvitacion:
    """Invita por email con `platform_auth.invitar_staff`; sus errores (`ErrorAuth`, `EmailInvalido`, ...) suben."""
    return await invitar_staff(email, permission_keys, nombre=nombre, sesion=sesion, pool=pool, cliente=cliente)


async def cambiar_permisos(
    conn: Any, sesion: SesionDashboard, staff_id: uuid.UUID, permission_keys: list[str]
) -> dict[str, Any]:
    objetivo = await _objetivo(conn, sesion.tenant_id, staff_id)
    pedidos = sorted(set(permission_keys))
    catalogo = {f["key"] for f in await conn.fetch(SQL_CATALOGO)}
    desconocidos = [c for c in pedidos if c not in catalogo]
    if desconocidos:
        raise ErrorPermisoDesconocido(desconocidos)
    actuales = {f["permission_key"] for f in await conn.fetch(SQL_PERMISOS_DE, objetivo["id"])}
    agregar = sorted(set(pedidos) - actuales)
    quitar = sorted(actuales - set(pedidos))
    if quitar:
        await conn.execute(SQL_QUITAR_PERMISOS, objetivo["id"], pedidos)
    if agregar:
        await conn.execute(SQL_AGREGAR_PERMISOS, objetivo["id"], agregar)
    if agregar or quitar:
        await registrar(
            conn,
            "staff_permissions_changed",
            "tenant_user",
            objetivo["id"],
            {"agregados": agregar, "quitados": quitar},
        )
    return {"id": str(objetivo["id"]), "permission_keys": pedidos}


async def cambiar_owner(conn: Any, sesion: SesionDashboard, staff_id: uuid.UUID, es_owner: bool) -> dict[str, Any]:
    objetivo = await _objetivo(conn, sesion.tenant_id, staff_id)
    if objetivo["is_owner"] is es_owner:
        return _estado(objetivo)
    if es_owner and objetivo["is_active"] is not True:
        raise ErrorStaffInactivo
    with _traducir_ultimo_owner():
        fila = await conn.fetchrow(SQL_CAMBIAR_OWNER, sesion.tenant_id, staff_id, es_owner)
    await registrar(conn, "staff_owner_granted" if es_owner else "staff_owner_revoked", "tenant_user", staff_id)
    return _estado(fila)


async def cambiar_activo(conn: Any, sesion: SesionDashboard, staff_id: uuid.UUID, activo: bool) -> dict[str, Any]:
    """Desactiva o reactiva. Si el objetivo es owner exige owner vivo (`ErrorAuth` si quien llama no lo es)."""
    objetivo = await _objetivo(conn, sesion.tenant_id, staff_id)
    if objetivo["is_owner"] is True:
        await verificar_owner_vivo(conn, sesion)
    if objetivo["is_active"] is activo:
        return _estado(objetivo)
    with _traducir_ultimo_owner():
        fila = await conn.fetchrow(SQL_CAMBIAR_ACTIVO, sesion.tenant_id, staff_id, activo)
    await registrar(conn, "staff_reactivated" if activo else "staff_deactivated", "tenant_user", staff_id)
    return _estado(fila)
