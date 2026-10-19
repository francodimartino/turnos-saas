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

El router es de módulo (`router = APIRouter()`): la fábrica de Calendar (`app.state.calendario`) y las opciones de
avisos (`app.state.opciones_avisos`) se resuelven con `Depends` en cada request.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from observabilidad import contexto_log
from platform_auth import ErrorAuth, IdentidadJWT

from dashboard_backend import gestion_turnos as g
from dashboard_backend.avisos_turnos import OpcionesAvisos
from dashboard_backend.cierre_turnos import CambioConfirmado, cerrar
from dashboard_backend.dependencias import (
    ACTOR_TYPE,
    Transaccion,
    dependencias_de,
    http_desde_error_auth,
    identidad_actual,
    pool_actual,
    transaccion_con_permiso,
)
from dashboard_backend.errores_db import reintentar_transaccion, traducir_errores_db
from dashboard_backend.esquemas_turnos import AltaPack, AltaTurno, MoverTurno

PERMISO_VER = g.PERMISO_VER
PERMISO_GESTIONAR = g.PERMISO_GESTIONAR

router = APIRouter(tags=["turnos"])


def fabrica_calendario(request: Request) -> Any:
    """Fábrica de servicios de Calendar de la app (`app.state.calendario`)."""
    return request.app.state.calendario


def opciones_avisos(request: Request) -> OpcionesAvisos:
    """Opciones de los avisos al cliente (`app.state.opciones_avisos`); sin ellas, las de producción."""
    return getattr(request.app.state, "opciones_avisos", None) or OpcionesAvisos()


Identidad = Annotated[IdentidadJWT, Depends(identidad_actual)]
Ver = Annotated[Transaccion, Depends(transaccion_con_permiso(PERMISO_VER))]
Gestionar = Annotated[Transaccion, Depends(transaccion_con_permiso(PERMISO_GESTIONAR))]
Pool = Annotated[Any, Depends(pool_actual)]
Fabrica = Annotated[Any, Depends(fabrica_calendario)]
Opciones = Annotated[OpcionesAvisos, Depends(opciones_avisos)]


def _http(e: g.ErrorTurno) -> HTTPException:
    return HTTPException(status_code=e.estado, detail={"codigo": e.codigo, "mensaje": e.texto})


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


async def _cerrar(
    pool: Any,
    opciones: OpcionesAvisos,
    fabrica: Any,
    sesion: Any,
    tareas: BackgroundTasks,
    cambio: CambioConfirmado,
) -> dict[str, Any]:
    with contexto_log(tenant_id=str(sesion.tenant_id), actor_type=ACTOR_TYPE):
        return await cerrar(pool, sesion, tareas, opciones, fabrica, cambio)


async def _cambiar(
    request: Request,
    identidad: IdentidadJWT,
    tareas: BackgroundTasks,
    pool: Any,
    opciones: OpcionesAvisos,
    fabrica: Any,
    turno_id: uuid.UUID,
    **regla: Any,
):
    deps = dependencias_de(request)
    sesion, cambio = await _transaccional(
        lambda: deps.abrir_sesion(identidad),
        lambda tx: g.cambiar_estado(tx.conn, tx.sesion, turno_id, **regla),
        repetir=False,
    )
    return await _cerrar(pool, opciones, fabrica, sesion, tareas, cambio)


@router.get("/turnos")
async def listar(
    tx: Ver,
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
async def detalle(turno_id: uuid.UUID, tx: Ver) -> dict[str, Any]:
    try:
        return await g.detalle(tx.conn, tx.sesion.tenant_id, turno_id)
    except g.ErrorTurno as e:
        raise _http(e) from None


@router.post("/turnos", status_code=201)
async def crear(
    request: Request,
    datos: AltaTurno,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    deps = dependencias_de(request)
    sesion, cambio = await _transaccional(
        lambda: deps.abrir_con_permiso(identidad, PERMISO_GESTIONAR),
        lambda tx: g.crear(tx.conn, tx.sesion.tenant_id, datos),
        repetir=True,
    )
    return await _cerrar(pool, opciones, fabrica, sesion, tareas, cambio)


@router.post("/turnos/{turno_id}/aprobar")
async def aprobar(
    request: Request,
    turno_id: uuid.UUID,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    return await _cambiar(
        request, identidad, tareas, pool, opciones, fabrica, turno_id,
        validos=("pending",), verbo="aprobar", nuevo="confirmed",
        accion_audit="booking_approved", plantilla="turno_confirmado", profesional_vinculado=True,
    )  # fmt: skip


@router.post("/turnos/{turno_id}/rechazar")
async def rechazar(
    request: Request,
    turno_id: uuid.UUID,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    return await _cambiar(
        request, identidad, tareas, pool, opciones, fabrica, turno_id,
        validos=("pending",), verbo="rechazar", nuevo="cancelled",
        accion_audit="booking_rejected", plantilla="turno_rechazado", profesional_vinculado=True,
    )  # fmt: skip


@router.post("/turnos/{turno_id}/cancelar")
async def cancelar(
    request: Request,
    turno_id: uuid.UUID,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    # La sesión del pack la devuelve el trigger de la base al pasar a 'cancelled': acá no se toca `used_sessions`.
    return await _cambiar(
        request, identidad, tareas, pool, opciones, fabrica, turno_id,
        validos=("pending", "confirmed"), verbo="cancelar", nuevo="cancelled",
        accion_audit="booking_cancelled", plantilla="turno_cancelado",
    )  # fmt: skip


@router.post("/turnos/{turno_id}/completar")
async def completar(
    request: Request,
    turno_id: uuid.UUID,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    return await _cambiar(
        request, identidad, tareas, pool, opciones, fabrica, turno_id,
        validos=("confirmed",), verbo="completar", nuevo="completed",
        accion_audit="booking_completed", plantilla=None,
    )  # fmt: skip


@router.post("/turnos/{turno_id}/mover")
async def mover(
    request: Request,
    turno_id: uuid.UUID,
    datos: MoverTurno,
    identidad: Identidad,
    tareas: BackgroundTasks,
    pool: Pool,
    opciones: Opciones,
    fabrica: Fabrica,
):
    deps = dependencias_de(request)
    sesion, cambio = await _transaccional(
        lambda: deps.abrir_sesion(identidad),
        lambda tx: g.mover(tx.conn, tx.sesion, turno_id, datos),
        repetir=True,
    )
    return await _cerrar(pool, opciones, fabrica, sesion, tareas, cambio)


@router.get("/packs")
async def listar_los_packs(
    tx: Ver,
    end_customer_id: uuid.UUID | None = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
) -> dict[str, Any]:
    return await g.listar_los_packs(tx.conn, tx.sesion.tenant_id, end_customer_id, limite, offset)


@router.post("/packs", status_code=201)
async def asignar_pack(datos: AltaPack, tx: Gestionar) -> dict[str, Any]:
    try:
        with traducir_errores_db():
            return await g.asignar_pack(tx.conn, tx.sesion.tenant_id, datos)
    except g.ErrorTurno as e:
        raise _http(e) from None
