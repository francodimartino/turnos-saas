"""Staff del local y sus permisos (ARCHITECTURE.md §2.4, §2.9, §3.2).

  GET  /staff                          manage_staff     lista del tenant con `nombre`, `email` y `permission_key`
  POST /staff/invitaciones             owner vivo       invita por email (y nombre completo) con un set de permisos
  PUT  /staff/{id}/permisos            owner vivo       reemplaza el set de permisos de otro (o propio)
  PUT  /staff/{id}/owner               owner vivo       da o quita `is_owner`
  POST /staff/{id}/desactivar          manage_staff     `is_active = false` (si el objetivo es owner: owner vivo)
  POST /staff/{id}/reactivar           manage_staff     `is_active = true`  (si el objetivo es owner: owner vivo)

Escalación de privilegios: `manage_staff` NO alcanza para dar permisos ni `is_owner` (un empleado con ese permiso
podría auto-promoverse); el chequeo es de owner vivo, en la misma transacción que la escritura. Tampoco alcanza para
desactivar o reactivar a un owner. Un staff desactivado pierde el acceso en su próximo request (el estado se lee en
vivo) y la baja nunca borra la fila: `dashboard_service` no tiene DELETE sobre `tenant_users` (§3.2).

El último owner activo lo protege un trigger de la base (no se puede desactivar ni quitarle `is_owner`, también ante
cambios simultáneos); acá solo se traduce su rechazo a 409 `ultimo_owner`.

Identidad: nombre y email se ven siempre (decisión de producto). Viven en `auth.users`, que este rol no lee: el listado
los pide a `public.get_tenant_staff_identity` (SECURITY DEFINER; solo responde por el tenant del contexto). Mientras la
persona no vincula su cuenta, el email es el de la invitación pendiente y el nombre es null. La respuesta nunca trae
el `auth_user_id`. El nombre y el email no van a `audit_log` ni a logs: cada cambio queda con ids y claves de permiso.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from asyncpg.exceptions import PostgresError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from platform_audit import registrar
from platform_auth import (
    ClienteAuthAdmin,
    EmailInvalido,
    ErrorAuth,
    ErrorInvitacion,
    NombreInvalido,
    PermisoDesconocido,
    SesionDashboard,
    SupabaseAuthAdmin,
    invitar_staff,
    verificar_owner_vivo,
)
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth
from dashboard_backend.escritura_sql import no_encontrado

_log = logging.getLogger("dashboard_backend.staff")

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

_MENSAJE_ULTIMO_OWNER = (
    "No se puede desactivar ni quitar el rol de owner al último owner activo del local. "
    "Antes hacé owner a otra persona de tu equipo."
)

Clave = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Invitacion(_Entrada):
    email: Annotated[str, StringConstraints(max_length=254)]
    # `Any` a propósito: la validación (obligatorio, 1 a 120 caracteres) la hace `platform_auth` y responde 422
    # `nombre_invalido` sin repetir el valor (la validación de pydantic lo devolvería en el error).
    nombre: Any = None
    permission_keys: list[Clave] = Field(default_factory=list, max_length=50)


class PermisosCuerpo(_Entrada):
    permission_keys: list[Clave] = Field(max_length=50)


class OwnerCuerpo(_Entrada):
    is_owner: StrictBool


def _error(estado: int, codigo: str, mensaje: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje, **extra})


@contextmanager
def _traducir_ultimo_owner() -> Iterator[None]:
    try:
        yield
    except PostgresError as e:
        if getattr(e, "sqlstate", None) == "23514" and "último owner" in str(e):
            raise _error(409, "ultimo_owner", _MENSAJE_ULTIMO_OWNER) from None
        raise


async def _objetivo(tx: Transaccion, staff_id: uuid.UUID) -> Any:
    fila = await tx.conn.fetchrow(SQL_OBJETIVO, tx.sesion.tenant_id, staff_id)
    if fila is None:
        raise no_encontrado("La persona del equipo")
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


async def _exigir_owner_si_el_objetivo_es_owner(tx: Transaccion, objetivo: Any) -> None:
    if objetivo["is_owner"] is True:
        try:
            await verificar_owner_vivo(tx.conn, tx.sesion)
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None


def crear_router_staff(
    deps: DependenciasDashboard,
    obtener_pool: Callable[[], Any],
    obtener_cliente_admin: Callable[[], ClienteAuthAdmin],
) -> APIRouter:
    router = APIRouter(tags=["staff"])
    Gestiona = Annotated[Transaccion, Depends(deps.tx_con_permiso("manage_staff"))]
    SoloOwner = Annotated[Transaccion, Depends(deps.tx_solo_owner)]
    Sesion = Annotated[SesionDashboard, Depends(deps.sesion)]

    @router.get("/staff")
    async def listar(tx: Gestiona):
        filas = await tx.conn.fetch(SQL_LISTAR, tx.sesion.tenant_id, tx.sesion.user_id)
        identidades = {i["tenant_user_id"]: i for i in await tx.conn.fetch(SQL_IDENTIDAD, tx.sesion.tenant_id)}
        return {"staff": [_item(f, identidades.get(f["id"])) for f in filas]}

    @router.post("/staff/invitaciones")
    async def invitar(datos: Invitacion, sesion: Sesion):
        # `invitar_staff` abre sus propias transacciones (valida owner vivo antes de llamar a la API de Auth).
        try:
            cliente = obtener_cliente_admin()
        except ValueError:
            _log.error("invitaciones_sin_configurar")
            raise _error(
                503, "invitaciones_no_configuradas", "No se pueden enviar invitaciones por ahora. Avisá a soporte."
            ) from None
        try:
            r = await invitar_staff(
                datos.email,
                datos.permission_keys,
                nombre=datos.nombre,
                sesion=sesion,
                pool=obtener_pool(),
                cliente=cliente,
            )
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None
        except EmailInvalido:
            raise _error(422, "email_invalido", "El email no tiene un formato válido.") from None
        except NombreInvalido as e:
            # el mensaje de `NombreInvalido` nunca repite el valor
            raise _error(422, "nombre_invalido", f"Nombre inválido: {e}.") from None
        except PermisoDesconocido:
            raise _error(422, "permiso_desconocido", "Alguno de los permisos indicados no existe.") from None
        except ErrorInvitacion as e:
            _log.warning("invitacion_fallida", extra={"reintentable": e.reintentable})
            if e.reintentable:
                raise _error(
                    503,
                    "invitacion_no_enviada",
                    "No pudimos enviar la invitación. Probá de nuevo en un rato.",
                    reintentable=True,
                ) from None
            raise _error(
                502, "invitacion_rechazada", "No pudimos enviar la invitación a ese email.", reintentable=False
            ) from None
        return JSONResponse(
            status_code=201 if r.creado else 200,
            content={"tenant_user_id": str(r.tenant_user_id), "creado": r.creado},
        )

    @router.put("/staff/{staff_id}/permisos")
    async def cambiar_permisos(staff_id: uuid.UUID, datos: PermisosCuerpo, tx: SoloOwner):
        objetivo = await _objetivo(tx, staff_id)
        pedidos = sorted(set(datos.permission_keys))
        catalogo = {f["key"] for f in await tx.conn.fetch(SQL_CATALOGO)}
        desconocidos = [c for c in pedidos if c not in catalogo]
        if desconocidos:
            raise _error(422, "permiso_desconocido", f"Permisos inexistentes: {', '.join(desconocidos)}.")
        actuales = {f["permission_key"] for f in await tx.conn.fetch(SQL_PERMISOS_DE, objetivo["id"])}
        agregar = sorted(set(pedidos) - actuales)
        quitar = sorted(actuales - set(pedidos))
        if quitar:
            await tx.conn.execute(SQL_QUITAR_PERMISOS, objetivo["id"], pedidos)
        if agregar:
            await tx.conn.execute(SQL_AGREGAR_PERMISOS, objetivo["id"], agregar)
        if agregar or quitar:
            await registrar(
                tx.conn,
                "staff_permissions_changed",
                "tenant_user",
                objetivo["id"],
                {"agregados": agregar, "quitados": quitar},
            )
        return {"id": str(objetivo["id"]), "permission_keys": pedidos}

    @router.put("/staff/{staff_id}/owner")
    async def cambiar_owner(staff_id: uuid.UUID, datos: OwnerCuerpo, tx: SoloOwner):
        objetivo = await _objetivo(tx, staff_id)
        if objetivo["is_owner"] is datos.is_owner:
            return _estado(objetivo)
        if datos.is_owner and objetivo["is_active"] is not True:
            raise _error(409, "staff_inactivo", "Primero reactivá a esa persona para hacerla owner.")
        with _traducir_ultimo_owner():
            fila = await tx.conn.fetchrow(SQL_CAMBIAR_OWNER, tx.sesion.tenant_id, staff_id, datos.is_owner)
        await registrar(
            tx.conn, "staff_owner_granted" if datos.is_owner else "staff_owner_revoked", "tenant_user", staff_id
        )
        return _estado(fila)

    async def _cambiar_activo(tx: Transaccion, staff_id: uuid.UUID, activo: bool):
        objetivo = await _objetivo(tx, staff_id)
        await _exigir_owner_si_el_objetivo_es_owner(tx, objetivo)
        if objetivo["is_active"] is activo:
            return _estado(objetivo)
        with _traducir_ultimo_owner():
            fila = await tx.conn.fetchrow(SQL_CAMBIAR_ACTIVO, tx.sesion.tenant_id, staff_id, activo)
        await registrar(tx.conn, "staff_reactivated" if activo else "staff_deactivated", "tenant_user", staff_id)
        return _estado(fila)

    @router.post("/staff/{staff_id}/desactivar")
    async def desactivar(staff_id: uuid.UUID, tx: Gestiona):
        return await _cambiar_activo(tx, staff_id, False)

    @router.post("/staff/{staff_id}/reactivar")
    async def reactivar(staff_id: uuid.UUID, tx: Gestiona):
        return await _cambiar_activo(tx, staff_id, True)

    return router


def cliente_admin_desde_entorno() -> ClienteAuthAdmin:
    """Auth Admin API con la `service_role` del entorno (no la lee `Config`). `ValueError` si falta alguna variable."""
    return SupabaseAuthAdmin.desde_entorno()
