"""Catálogo del local: servicios, profesionales y qué servicios hace cada profesional (ARCHITECTURE.md §7.1).

Permisos (§2.4; el owner vivo siempre los tiene). Las lecturas las puede hacer cualquier staff activo del tenant:

  GET    /servicios, /servicios/{id}                         cualquier staff
  POST   /servicios                                          edit_prices
  PATCH  /servicios/{id}                                     edit_prices
  DELETE /servicios/{id}                                     edit_prices
  GET    /profesionales, /profesionales/{id}                 cualquier staff
  POST   /profesionales                                      manage_staff (vincular una cuenta con `tenant_user_id`:
                                                             solo el owner vivo; sin vínculo basta manage_staff)
  PATCH  /profesionales/{id}                                 manage_staff (is_active=false dispara en la base el
                                                             borrado del secret de Calendar del profesional);
                                                             cambiar `tenant_user_id` (vincular, re-vincular o
                                                             desvincular) solo lo hace el owner vivo (403 si no)
  DELETE /profesionales/{id}                                 manage_staff (409 si tiene turnos u otros datos)
  GET    /profesionales/{id}/servicios                       cualquier staff
  PUT    /profesionales/{id}/servicios/{service_id}          manage_staff; `confirmation_mode` auto|approval
                                                             (`deposit` se rechaza con 422 en el piloto)
  DELETE /profesionales/{id}/servicios/{service_id}          manage_staff

Servicios exige `edit_prices` para todo lo que escribe, no solo para el precio: un servicio es nombre, duración y
precio juntos, y separar los permisos por campo no aporta nada que el piloto necesite.

Auditoría: el alta, la edición y la baja de servicios y de profesionales (y la desactivación de un profesional)
quedan en `audit_log` vía `platform_audit.registrar`, dentro de la misma transacción del cambio (si el registro
falla, el cambio se revierte). La metadata lleva qué campos cambiaron (nombres de columna, nunca sus valores) y, para
servicios, el precio anterior y el nuevo; no lleva nombres ni datos personales. El actor es el staff de la sesión.

El vínculo profesional-cuenta decide con qué cuenta se aprueban turnos como ese profesional, por eso cambiarlo es
una escalación de privilegios: se exige owner vivo (leído en la base, en la misma transacción del cambio) y el
audit lleva `vinculo: {anterior, nuevo}` con ids de `tenant_users`.

Todo corre dentro de una transacción con el tenant de la sesión; cada query filtra además `tenant_id = $1` (la RLS es
el límite, no la única capa). Lo ajeno o inexistente es 404, nunca 403: no se revela qué existe en otro tenant.

Este módulo solo traduce HTTP: las reglas, el SQL y la auditoría están en `catalogo_servicios`,
`catalogo_profesionales` y `catalogo_asignaciones`, que no conocen FastAPI. Cada endpoint usa una única transacción
(la dependencia `transaccion*`) para la sesión viva, el permiso y la escritura.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from platform_auth import Permiso
from turnos_http import http_desde_error_auth, no_encontrado

from dashboard_backend import catalogo_asignaciones, catalogo_profesionales, catalogo_servicios
from dashboard_backend.catalogo_asignaciones import ErrorAsignacionInexistente
from dashboard_backend.catalogo_profesionales import ErrorProfesionalInexistente, ErrorSoloOwner
from dashboard_backend.catalogo_servicios import ErrorServicioInexistente
from dashboard_backend.dependencias import Transaccion, transaccion, transaccion_con_permiso
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.esquemas_catalogo import (
    ProfesionalIn,
    ProfesionalParche,
    ServicioDeProfesionalIn,
    ServicioIn,
    ServicioParche,
)
from dashboard_backend.respuestas_catalogo import AsignacionOut, ProfesionalOut, ServicioOut

router = APIRouter(tags=["catalogo"])

# Con nombre para poder reemplazarlas en `app.dependency_overrides`.
tx_edita_precios = transaccion_con_permiso(Permiso.EDITAR_PRECIOS)
tx_gestiona_personal = transaccion_con_permiso(Permiso.GESTIONAR_PERSONAL)

Lectura = Annotated[Transaccion, Depends(transaccion)]
EditaPrecios = Annotated[Transaccion, Depends(tx_edita_precios)]
GestionaPersonal = Annotated[Transaccion, Depends(tx_gestiona_personal)]
Limite = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=2_147_483_647)]

SERVICIO = "El servicio"
PROFESIONAL = "El profesional"
ASIGNACION = "La asignación del servicio al profesional"


def _no_es_owner(e: ErrorSoloOwner) -> HTTPException:
    return http_desde_error_auth(e.error)


# --- servicios ----------------------------------------------------------------------------------------------


@router.get("/servicios", response_model=list[ServicioOut])
async def listar_los_servicios(
    tx: Lectura, incluir_inactivos: bool = False, limite: Limite = 100, offset: Offset = 0
) -> Any:
    return await catalogo_servicios.listar_servicios(tx.conn, tx.sesion.tenant_id, incluir_inactivos, limite, offset)


@router.get("/servicios/{servicio_id}", response_model=ServicioOut)
async def ver_servicio(servicio_id: uuid.UUID, tx: Lectura) -> Any:
    try:
        return await catalogo_servicios.ver_servicio(tx.conn, tx.sesion.tenant_id, servicio_id)
    except ErrorServicioInexistente:
        raise no_encontrado(SERVICIO) from None


@router.post("/servicios", status_code=201, response_model=ServicioOut)
async def crear_servicio(datos: ServicioIn, tx: EditaPrecios) -> Any:
    with traducir_errores_db():
        return await catalogo_servicios.crear_servicio(
            tx.conn,
            tx.sesion.tenant_id,
            name=datos.name,
            duration_minutes=datos.duration_minutes,
            price=datos.price,
            deposit_amount=datos.deposit_amount,
            is_active=datos.is_active,
            campos=set(datos.model_fields_set),
        )


@router.patch("/servicios/{servicio_id}", response_model=ServicioOut)
async def modificar_servicio(servicio_id: uuid.UUID, datos: ServicioParche, tx: EditaPrecios) -> Any:
    try:
        with traducir_errores_db():
            return await catalogo_servicios.modificar_servicio(
                tx.conn, tx.sesion.tenant_id, servicio_id, datos.cambios()
            )
    except ErrorServicioInexistente:
        raise no_encontrado(SERVICIO) from None


@router.delete("/servicios/{servicio_id}", status_code=204, response_class=Response)
async def borrar_servicio(servicio_id: uuid.UUID, tx: EditaPrecios):
    try:
        with traducir_errores_db(en_borrado=True):
            await catalogo_servicios.borrar_servicio(tx.conn, tx.sesion.tenant_id, servicio_id)
    except ErrorServicioInexistente:
        raise no_encontrado(SERVICIO) from None


# --- profesionales ------------------------------------------------------------------------------------------


@router.get("/profesionales", response_model=list[ProfesionalOut])
async def listar_los_profesionales(
    tx: Lectura, incluir_inactivos: bool = False, limite: Limite = 100, offset: Offset = 0
) -> Any:
    return await catalogo_profesionales.listar_profesionales(
        tx.conn, tx.sesion.tenant_id, incluir_inactivos, limite, offset
    )


@router.get("/profesionales/{profesional_id}", response_model=ProfesionalOut)
async def ver_profesional(profesional_id: uuid.UUID, tx: Lectura) -> Any:
    try:
        return await catalogo_profesionales.ver_profesional(tx.conn, tx.sesion.tenant_id, profesional_id)
    except ErrorProfesionalInexistente:
        raise no_encontrado(PROFESIONAL) from None


@router.post("/profesionales", status_code=201, response_model=ProfesionalOut)
async def crear_profesional(datos: ProfesionalIn, tx: GestionaPersonal) -> Any:
    try:
        with traducir_errores_db():
            return await catalogo_profesionales.crear_profesional(
                tx.conn,
                tx.sesion,
                name=datos.name,
                tenant_user_id=datos.tenant_user_id,
                is_active=datos.is_active,
                campos=set(datos.model_fields_set),
            )
    except ErrorSoloOwner as e:
        raise _no_es_owner(e) from None


@router.patch("/profesionales/{profesional_id}", response_model=ProfesionalOut)
async def modificar_profesional(profesional_id: uuid.UUID, datos: ProfesionalParche, tx: GestionaPersonal) -> Any:
    try:
        with traducir_errores_db():
            return await catalogo_profesionales.modificar_profesional(
                tx.conn, tx.sesion, profesional_id, datos.cambios()
            )
    except ErrorSoloOwner as e:
        raise _no_es_owner(e) from None
    except ErrorProfesionalInexistente:
        raise no_encontrado(PROFESIONAL) from None


@router.delete("/profesionales/{profesional_id}", status_code=204, response_class=Response)
async def borrar_profesional(profesional_id: uuid.UUID, tx: GestionaPersonal):
    try:
        with traducir_errores_db(en_borrado=True):
            await catalogo_profesionales.borrar_profesional(tx.conn, tx.sesion.tenant_id, profesional_id)
    except ErrorProfesionalInexistente:
        raise no_encontrado(PROFESIONAL) from None


# --- professional_services ----------------------------------------------------------------------------------


@router.get("/profesionales/{profesional_id}/servicios", response_model=list[AsignacionOut])
async def servicios_del_profesional(profesional_id: uuid.UUID, tx: Lectura) -> Any:
    try:
        return await catalogo_asignaciones.servicios_del_profesional(tx.conn, tx.sesion.tenant_id, profesional_id)
    except ErrorProfesionalInexistente:
        raise no_encontrado(PROFESIONAL) from None


@router.put("/profesionales/{profesional_id}/servicios/{servicio_id}", response_model=AsignacionOut)
async def asignar_servicio(
    profesional_id: uuid.UUID, servicio_id: uuid.UUID, datos: ServicioDeProfesionalIn, tx: GestionaPersonal
) -> Any:
    try:
        with traducir_errores_db():
            return await catalogo_asignaciones.asignar_servicio(
                tx.conn, tx.sesion.tenant_id, profesional_id, servicio_id, datos.confirmation_mode
            )
    except ErrorProfesionalInexistente:
        raise no_encontrado(PROFESIONAL) from None
    except ErrorServicioInexistente:
        raise no_encontrado(SERVICIO) from None


@router.delete("/profesionales/{profesional_id}/servicios/{servicio_id}", status_code=204, response_class=Response)
async def quitar_servicio(profesional_id: uuid.UUID, servicio_id: uuid.UUID, tx: GestionaPersonal):
    try:
        with traducir_errores_db(en_borrado=True):
            await catalogo_asignaciones.quitar_servicio(tx.conn, tx.sesion.tenant_id, profesional_id, servicio_id)
    except ErrorAsignacionInexistente:
        raise no_encontrado(ASIGNACION) from None
