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

El último owner activo lo protege un trigger de la base; acá solo se traduce su rechazo a 409 `ultimo_owner`.

Identidad: nombre y email se ven siempre (decisión de producto). La respuesta nunca trae el `auth_user_id`.

Este módulo solo traduce HTTP: las reglas, el SQL y la auditoría están en `staff`, que no conoce FastAPI. Cada
endpoint usa una única transacción (la dependencia `transaccion*`) para la sesión viva, el permiso y la escritura.
La excepción es la invitación, que no retiene una conexión mientras espera a la Auth Admin API (ver `staff`).
El cliente de Auth Admin lo entrega `Depends(cliente_admin)` desde `app.state.cliente_admin`.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from platform_auth import (
    ClienteAuthAdmin,
    EmailInvalido,
    ErrorAuth,
    ErrorInvitacion,
    NombreInvalido,
    PermisoDesconocido,
    SesionDashboard,
)
from turnos_http import error_http, http_desde_error_auth, no_encontrado

from dashboard_backend import staff
from dashboard_backend.dependencias import (
    Transaccion,
    identidad_actual,
    pool_actual,
    sesion_actual,
    transaccion_con_permiso,
    transaccion_solo_owner,
)
from dashboard_backend.esquemas_staff import Invitacion, OwnerCuerpo, PermisosCuerpo
from dashboard_backend.staff import (
    ErrorPermisoDesconocido,
    ErrorStaffInactivo,
    ErrorStaffInexistente,
    ErrorUltimoOwner,
)

_log = logging.getLogger("dashboard_backend.staff")

router = APIRouter(tags=["staff"])

# Con nombre para poder reemplazarlas en `app.dependency_overrides`.
tx_gestiona_personal = transaccion_con_permiso("manage_staff")
tx_solo_owner = transaccion_solo_owner

_MENSAJE_ULTIMO_OWNER = (
    "No se puede desactivar ni quitar el rol de owner al último owner activo del local. "
    "Antes hacé owner a otra persona de tu equipo."
)


def cliente_admin(request: Request, _identidad: Annotated[Any, Depends(identidad_actual)]) -> ClienteAuthAdmin:
    """Auth Admin API de la app (`app.state.cliente_admin`); 503 si faltan sus variables de entorno."""
    try:
        return request.app.state.cliente_admin()
    except ValueError:
        _log.error("invitaciones_sin_configurar")
        raise error_http(
            503, "invitaciones_no_configuradas", "No se pueden enviar invitaciones por ahora. Avisá a soporte."
        ) from None


GestionaPersonal = Annotated[Transaccion, Depends(tx_gestiona_personal)]
SoloOwner = Annotated[Transaccion, Depends(tx_solo_owner)]
Sesion = Annotated[SesionDashboard, Depends(sesion_actual)]
ClienteAdmin = Annotated[ClienteAuthAdmin, Depends(cliente_admin)]
Pool = Annotated[Any, Depends(pool_actual)]


@router.get("/staff")
async def listar(tx: GestionaPersonal):
    return await staff.listar_staff(tx.conn, tx.sesion)


@router.post("/staff/invitaciones")
async def invitar(datos: Invitacion, sesion: Sesion, cliente: ClienteAdmin, pool: Pool):
    try:
        r = await staff.invitar(
            datos.email, datos.permission_keys, datos.nombre, sesion=sesion, pool=pool, cliente=cliente
        )
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None
    except EmailInvalido:
        raise error_http(422, "email_invalido", "El email no tiene un formato válido.") from None
    except NombreInvalido as e:
        # el mensaje de `NombreInvalido` nunca repite el valor
        raise error_http(422, "nombre_invalido", f"Nombre inválido: {e}.") from None
    except PermisoDesconocido:
        raise error_http(422, "permiso_desconocido", "Alguno de los permisos indicados no existe.") from None
    except ErrorInvitacion as e:
        _log.warning("invitacion_fallida", extra={"reintentable": e.reintentable})
        if e.reintentable:
            raise error_http(
                503,
                "invitacion_no_enviada",
                "No pudimos enviar la invitación. Probá de nuevo en un rato.",
                reintentable=True,
            ) from None
        raise error_http(
            502, "invitacion_rechazada", "No pudimos enviar la invitación a ese email.", reintentable=False
        ) from None
    return JSONResponse(
        status_code=201 if r.creado else 200,
        content={"tenant_user_id": str(r.tenant_user_id), "creado": r.creado},
    )


@router.put("/staff/{staff_id}/permisos")
async def cambiar_permisos(staff_id: uuid.UUID, datos: PermisosCuerpo, tx: SoloOwner):
    try:
        return await staff.cambiar_permisos(tx.conn, tx.sesion, staff_id, datos.permission_keys)
    except ErrorStaffInexistente:
        raise no_encontrado("La persona del equipo") from None
    except ErrorPermisoDesconocido as e:
        raise error_http(422, "permiso_desconocido", f"Permisos inexistentes: {', '.join(e.claves)}.") from None


@router.put("/staff/{staff_id}/owner")
async def cambiar_owner(staff_id: uuid.UUID, datos: OwnerCuerpo, tx: SoloOwner):
    try:
        return await staff.cambiar_owner(tx.conn, tx.sesion, staff_id, datos.is_owner)
    except ErrorStaffInexistente:
        raise no_encontrado("La persona del equipo") from None
    except ErrorStaffInactivo:
        raise error_http(409, "staff_inactivo", "Primero reactivá a esa persona para hacerla owner.") from None
    except ErrorUltimoOwner:
        raise error_http(409, "ultimo_owner", _MENSAJE_ULTIMO_OWNER) from None


async def _cambiar_activo(tx: Transaccion, staff_id: uuid.UUID, activo: bool):
    try:
        return await staff.cambiar_activo(tx.conn, tx.sesion, staff_id, activo)
    except ErrorStaffInexistente:
        raise no_encontrado("La persona del equipo") from None
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None
    except ErrorUltimoOwner:
        raise error_http(409, "ultimo_owner", _MENSAJE_ULTIMO_OWNER) from None


@router.post("/staff/{staff_id}/desactivar")
async def desactivar(staff_id: uuid.UUID, tx: GestionaPersonal):
    return await _cambiar_activo(tx, staff_id, False)


@router.post("/staff/{staff_id}/reactivar")
async def reactivar(staff_id: uuid.UUID, tx: GestionaPersonal):
    return await _cambiar_activo(tx, staff_id, True)
