"""Disponibilidad: reglas semanales por profesional y bloqueos puntuales (ARCHITECTURE.md §7.1).

Permisos (§2.4; el owner vivo siempre los tiene). Las lecturas las puede hacer cualquier staff activo del tenant:

  GET    /reglas-disponibilidad[?professional_id=], /reglas-disponibilidad/{id}   cualquier staff
  POST   /reglas-disponibilidad                                                   manage_schedule
  PATCH  /reglas-disponibilidad/{id}                                              manage_schedule
  DELETE /reglas-disponibilidad/{id}                                              manage_schedule
  GET    /bloqueos-disponibilidad[?professional_id=&desde=&hasta=], .../{id}      cualquier staff
  POST   /bloqueos-disponibilidad                                                 manage_schedule
  PATCH  /bloqueos-disponibilidad/{id}                                            manage_schedule
  DELETE /bloqueos-disponibilidad/{id}                                            manage_schedule

Reglas: `day_of_week` 0 = domingo ... 6 = sábado; `start_time`/`end_time` son hora local del local (sin zona).
Bloqueos: `start_at`/`end_at` con zona horaria; el rango es [inicio, fin). Con `professional_id` null el bloqueo es de
todo el local. Un profesional de otro tenant (o inexistente) es 422 `referencia_invalida`; una regla o bloqueo de otro
tenant es 404. Ventana invertida, día fuera de 0-6 y rango vacío los rechazan los `check` de la base (422 con mensaje).

Este módulo solo traduce HTTP: las reglas, el SQL y la auditoría están en `disponibilidad`, que no conoce FastAPI.
Cada endpoint usa una única transacción (la dependencia `transaccion*`) para la sesión viva, el permiso y la escritura.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from turnos_http import no_encontrado

from dashboard_backend import disponibilidad
from dashboard_backend.dependencias import Transaccion, transaccion, transaccion_con_permiso
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.esquemas_catalogo import BloqueoIn, BloqueoParche, ReglaIn, ReglaParche

router = APIRouter(tags=["disponibilidad"])

# Con nombre para poder reemplazarla en `app.dependency_overrides`.
tx_gestiona_agenda = transaccion_con_permiso("manage_schedule")

Lectura = Annotated[Transaccion, Depends(transaccion)]
GestionaAgenda = Annotated[Transaccion, Depends(tx_gestiona_agenda)]
Limite = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0, le=2_147_483_647)]

REGLA = "La regla de disponibilidad"
BLOQUEO = "El bloqueo"

# --- reglas semanales -------------------------------------------------------------------------------------------


@router.get("/reglas-disponibilidad")
async def listar_reglas(
    tx: Lectura, professional_id: uuid.UUID | None = None, limite: Limite = 200, offset: Offset = 0
):
    return await disponibilidad.listar_reglas(tx.conn, tx.sesion.tenant_id, professional_id, limite, offset)


@router.get("/reglas-disponibilidad/{regla_id}")
async def ver_regla(regla_id: uuid.UUID, tx: Lectura):
    try:
        return await disponibilidad.ver_regla(tx.conn, tx.sesion.tenant_id, regla_id)
    except disponibilidad.ErrorReglaInexistente:
        raise no_encontrado(REGLA) from None


@router.post("/reglas-disponibilidad", status_code=201)
async def crear_regla(datos: ReglaIn, tx: GestionaAgenda):
    with traducir_errores_db():
        return await disponibilidad.crear_regla(
            tx.conn, tx.sesion.tenant_id, datos.professional_id, datos.day_of_week, datos.start_time, datos.end_time
        )


@router.patch("/reglas-disponibilidad/{regla_id}")
async def modificar_regla(regla_id: uuid.UUID, datos: ReglaParche, tx: GestionaAgenda):
    try:
        with traducir_errores_db():
            return await disponibilidad.modificar_regla(tx.conn, tx.sesion.tenant_id, regla_id, datos.cambios())
    except disponibilidad.ErrorReglaInexistente:
        raise no_encontrado(REGLA) from None


@router.delete("/reglas-disponibilidad/{regla_id}", status_code=204, response_class=Response)
async def borrar_regla(regla_id: uuid.UUID, tx: GestionaAgenda):
    try:
        with traducir_errores_db(en_borrado=True):
            await disponibilidad.borrar_regla(tx.conn, tx.sesion.tenant_id, regla_id)
    except disponibilidad.ErrorReglaInexistente:
        raise no_encontrado(REGLA) from None


# --- bloqueos ---------------------------------------------------------------------------------------------------


@router.get("/bloqueos-disponibilidad")
async def listar_bloqueos(
    tx: Lectura,
    professional_id: uuid.UUID | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    limite: Limite = 200,
    offset: Offset = 0,
):
    return await disponibilidad.listar_bloqueos(
        tx.conn, tx.sesion.tenant_id, professional_id, desde, hasta, limite, offset
    )


@router.get("/bloqueos-disponibilidad/{bloqueo_id}")
async def ver_bloqueo(bloqueo_id: uuid.UUID, tx: Lectura):
    try:
        return await disponibilidad.ver_bloqueo(tx.conn, tx.sesion.tenant_id, bloqueo_id)
    except disponibilidad.ErrorBloqueoInexistente:
        raise no_encontrado(BLOQUEO) from None


@router.post("/bloqueos-disponibilidad", status_code=201)
async def crear_bloqueo(datos: BloqueoIn, tx: GestionaAgenda):
    with traducir_errores_db():
        return await disponibilidad.crear_bloqueo(
            tx.conn, tx.sesion.tenant_id, datos.professional_id, datos.start_at, datos.end_at, datos.reason
        )


@router.patch("/bloqueos-disponibilidad/{bloqueo_id}")
async def modificar_bloqueo(bloqueo_id: uuid.UUID, datos: BloqueoParche, tx: GestionaAgenda):
    try:
        with traducir_errores_db():
            return await disponibilidad.modificar_bloqueo(tx.conn, tx.sesion.tenant_id, bloqueo_id, datos.cambios())
    except disponibilidad.ErrorBloqueoInexistente:
        raise no_encontrado(BLOQUEO) from None


@router.delete("/bloqueos-disponibilidad/{bloqueo_id}", status_code=204, response_class=Response)
async def borrar_bloqueo(bloqueo_id: uuid.UUID, tx: GestionaAgenda):
    try:
        with traducir_errores_db(en_borrado=True):
            await disponibilidad.borrar_bloqueo(tx.conn, tx.sesion.tenant_id, bloqueo_id)
    except disponibilidad.ErrorBloqueoInexistente:
        raise no_encontrado(BLOQUEO) from None
