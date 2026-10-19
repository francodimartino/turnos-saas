"""Endpoints `/turnos` y `/packs`: gestión de la agenda desde el dashboard (ARCHITECTURE.md §7.1, §2.6).

Permisos (§2.4), chequeados en vivo dentro de la misma transacción del endpoint (el owner vivo los tiene todos):

  GET  /turnos, GET /turnos/{id}, GET /packs      `view_bookings`
  POST /turnos                                    `approve_bookings`  (alta del negocio: nace `confirmed`)
  POST /turnos/{id}/aprobar | /rechazar           `approve_bookings`, o el profesional vinculado al turno
                                                  (`professionals.tenant_user_id`); solo sobre turnos `pending`
  POST /turnos/{id}/cancelar | /mover | /completar `approve_bookings`  (el negocio es el único que cancela y mueve
                                                  en el piloto; mover y completar solo sobre `confirmed`)
  POST /packs                                     `approve_bookings`  (siempre `origin='manual_grant'`)

No hay DELETE: los turnos se cancelan. Un turno o cliente de otro tenant es indistinguible de uno inexistente (404).

Este módulo solo traduce HTTP: las reglas y el SQL están en `gestion_turnos` y `turnos`, la etapa posterior al COMMIT
en `cierre_turnos` y los avisos en `avisos_turnos`, que no conocen FastAPI.

Cada cambio corre en dos etapas. Primero UNA transacción (una sola conexión) que cubre la sesión viva, el permiso, el
bloqueo del turno, el chequeo de estado, el cambio, el `audit_log` y el aviso encolado en el outbox, y hace COMMIT al
salir. Recién después, con el cambio confirmado, `cierre_turnos.cerrar` sincroniza Google Calendar y programa los
envíos sin ninguna conexión tomada mientras se espera a la red. Esa segunda etapa, con sus transacciones cortas
propias (incluido el audit de un fallo de Calendar), es la única excepción a "una transacción por request". Un fallo
de Calendar o del aviso nunca revierte ni oculta un cambio ya confirmado: la respuesta dice qué pasó
(`calendario`, `aviso`).

El alta y el movimiento repiten la transacción entera ante un deadlock o un fallo de serialización.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from observabilidad import contexto_log
from platform_auth import ErrorAuth, IdentidadJWT

from dashboard_backend import gestion_turnos as g
from dashboard_backend.avisos_turnos import OpcionesAvisos
from dashboard_backend.calendario import FabricaCalendario
from dashboard_backend.cierre_turnos import CambioConfirmado, cerrar
from dashboard_backend.dependencias import (
    ACTOR_TYPE,
    DependenciasDashboard,
    Transaccion,
    http_desde_error_auth,
)
from dashboard_backend.errores_db import reintentar_transaccion, traducir_errores_db
from dashboard_backend.esquemas_turnos import AltaPack, AltaTurno, MoverTurno

PERMISO_VER = g.PERMISO_VER
PERMISO_GESTIONAR = g.PERMISO_GESTIONAR

__all__ = ["OpcionesAvisos", "crear_router_turnos"]


def _http(e: g.ErrorTurno) -> HTTPException:
    return HTTPException(status_code=e.estado, detail={"codigo": e.codigo, "mensaje": e.texto})


def crear_router_turnos(
    deps: DependenciasDashboard,
    obtener_pool: Callable[[], Any],
    fabrica_calendario: FabricaCalendario,
    opciones_avisos: OpcionesAvisos | None = None,
) -> APIRouter:
    router = APIRouter(tags=["turnos"])
    ver = Depends(deps.tx_con_permiso(PERMISO_VER))
    gestionar = Depends(deps.tx_con_permiso(PERMISO_GESTIONAR))
    identidad_dep = Depends(deps.identidad)
    opciones = opciones_avisos or OpcionesAvisos()

    async def _transaccional(
        abrir: Callable[[], Any], operacion: Callable[[Transaccion], Awaitable[Any]], *, repetir: bool
    ):
        """Una transacción: sesión, permiso y cambio. Devuelve `(sesion, resultado)` ya con COMMIT hecho."""

        async def intento() -> tuple[Any, Any]:
            try:
                async with abrir() as tx:
                    with traducir_errores_db():
                        return tx.sesion, await operacion(tx)
            except g.ErrorTurno as e:
                raise _http(e) from None
            except ErrorAuth as e:
                raise http_desde_error_auth(e) from None

        return await (reintentar_transaccion(intento) if repetir else intento())

    async def _cerrar(sesion: Any, tareas: BackgroundTasks, cambio: CambioConfirmado) -> dict[str, Any]:
        with contexto_log(tenant_id=str(sesion.tenant_id), actor_type=ACTOR_TYPE):
            return await cerrar(obtener_pool(), sesion, tareas, opciones, fabrica_calendario, cambio)

    async def _cambiar(identidad: IdentidadJWT, tareas: BackgroundTasks, turno_id: uuid.UUID, **regla: Any):
        sesion, cambio = await _transaccional(
            lambda: deps.abrir_sesion(identidad),
            lambda tx: g.cambiar_estado(tx.conn, tx.sesion, turno_id, **regla),
            repetir=False,
        )
        return await _cerrar(sesion, tareas, cambio)

    @router.get("/turnos")
    async def listar(
        tx: Annotated[Transaccion, ver],
        desde: Annotated[datetime, Query()],
        hasta: Annotated[datetime, Query()],
        estado: Annotated[list[str] | None, Query()] = None,
        professional_id: uuid.UUID | None = None,
        limite: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> dict[str, Any]:
        try:
            return await g.listar(tx.conn, tx.sesion.tenant_id, desde, hasta, estado, professional_id, limite, offset)
        except g.ErrorTurno as e:
            raise _http(e) from None

    @router.get("/turnos/{turno_id}")
    async def detalle(turno_id: uuid.UUID, tx: Annotated[Transaccion, ver]) -> dict[str, Any]:
        try:
            return await g.detalle(tx.conn, tx.sesion.tenant_id, turno_id)
        except g.ErrorTurno as e:
            raise _http(e) from None

    @router.post("/turnos", status_code=201)
    async def crear(datos: AltaTurno, identidad: Annotated[IdentidadJWT, identidad_dep], tareas: BackgroundTasks):
        sesion, cambio = await _transaccional(
            lambda: deps.abrir_con_permiso(identidad, PERMISO_GESTIONAR),
            lambda tx: g.crear(tx.conn, tx.sesion.tenant_id, datos),
            repetir=True,
        )
        return await _cerrar(sesion, tareas, cambio)

    @router.post("/turnos/{turno_id}/aprobar")
    async def aprobar(turno_id: uuid.UUID, identidad: Annotated[IdentidadJWT, identidad_dep], tareas: BackgroundTasks):
        return await _cambiar(
            identidad, tareas, turno_id, validos=("pending",), verbo="aprobar", nuevo="confirmed",
            accion_audit="booking_approved", plantilla="turno_confirmado", profesional_vinculado=True,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/rechazar")
    async def rechazar(turno_id: uuid.UUID, identidad: Annotated[IdentidadJWT, identidad_dep], tareas: BackgroundTasks):
        return await _cambiar(
            identidad, tareas, turno_id, validos=("pending",), verbo="rechazar", nuevo="cancelled",
            accion_audit="booking_rejected", plantilla="turno_rechazado", profesional_vinculado=True,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/cancelar")
    async def cancelar(turno_id: uuid.UUID, identidad: Annotated[IdentidadJWT, identidad_dep], tareas: BackgroundTasks):
        # La sesión del pack la devuelve el trigger de la base al pasar a 'cancelled': acá no se toca `used_sessions`.
        return await _cambiar(
            identidad, tareas, turno_id, validos=("pending", "confirmed"), verbo="cancelar", nuevo="cancelled",
            accion_audit="booking_cancelled", plantilla="turno_cancelado",
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/completar")
    async def completar(
        turno_id: uuid.UUID, identidad: Annotated[IdentidadJWT, identidad_dep], tareas: BackgroundTasks
    ):
        return await _cambiar(
            identidad, tareas, turno_id, validos=("confirmed",), verbo="completar", nuevo="completed",
            accion_audit="booking_completed", plantilla=None,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/mover")
    async def mover(
        turno_id: uuid.UUID,
        datos: MoverTurno,
        identidad: Annotated[IdentidadJWT, identidad_dep],
        tareas: BackgroundTasks,
    ):
        sesion, cambio = await _transaccional(
            lambda: deps.abrir_sesion(identidad),
            lambda tx: g.mover(tx.conn, tx.sesion, turno_id, datos),
            repetir=True,
        )
        return await _cerrar(sesion, tareas, cambio)

    @router.get("/packs")
    async def listar_los_packs(
        tx: Annotated[Transaccion, ver],
        end_customer_id: uuid.UUID | None = None,
        limite: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> dict[str, Any]:
        return await g.listar_los_packs(tx.conn, tx.sesion.tenant_id, end_customer_id, limite, offset)

    @router.post("/packs", status_code=201)
    async def asignar_pack(datos: AltaPack, tx: Annotated[Transaccion, gestionar]) -> dict[str, Any]:
        try:
            with traducir_errores_db():
                return await g.asignar_pack(tx.conn, tx.sesion.tenant_id, datos)
        except g.ErrorTurno as e:
            raise _http(e) from None

    return router
