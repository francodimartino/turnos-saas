"""Etapa posterior al COMMIT de un cambio de turno: Google Calendar, avisos y respuesta (ARCHITECTURE.md §7.1, §2.6).

Sin FastAPI. Se llama con el cambio ya confirmado, fuera de la transacción de la request, y nunca lanza por un fallo
de Calendar o de los avisos: el cambio no se revierte ni se oculta, la respuesta dice qué pasó (`calendario`,
`aviso`) y el rastro queda en los logs y en `audit_log` (`calendar_sync_failed`).

Es la excepción deliberada a "una transacción por request": sincronizar Calendar espera a Google, y ninguna
conexión puede quedar tomada durante esa espera. Por eso el servicio lee la base con transacciones cortas propias
(`ConexionBajoDemanda`), el id del evento se guarda en otra transacción corta y el audit de un fallo de Calendar
(`auditar_aparte`) va en la suya: su propio fallo tampoco puede deshacer un cambio ya confirmado. Evento solo en
`confirmed`: se crea al confirmar, se actualiza al mover y se borra al cancelar.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from platform_audit import registrar
from platform_auth import SesionDashboard
from platform_tenancy import ConexionBajoDemanda
from turnos_dominio import AvisoLiberado

from dashboard_backend import turnos as t
from dashboard_backend.avisos_turnos import (
    SIN_AVISO,
    AvisoEncolado,
    OpcionesAvisos,
    abrir_tx,
    avisar,
    avisar_liberados,
)
from dashboard_backend.calendario import FabricaCalendario

_log = logging.getLogger("dashboard_backend.turnos")


@dataclass(frozen=True)
class CambioConfirmado:
    """Lo que dejó una transacción de cambio ya hecha: la fila original y lo que hay que hacer después del COMMIT."""

    fila: Any
    estado: str
    inicio: datetime
    fin: datetime
    sincronizar: bool
    plantilla: str | None
    aviso: AvisoEncolado = SIN_AVISO
    liberados: Sequence[AvisoLiberado] = ()


async def auditar_aparte(pool: Any, sesion: SesionDashboard, accion: str, turno_id: uuid.UUID, meta: dict) -> None:
    """Registra en `audit_log` en una transacción propia. Nunca lanza."""
    try:
        async with abrir_tx(pool, sesion) as conn:
            await registrar(conn, accion, "bookings", turno_id, meta)
    except Exception as exc:  # noqa: BLE001 - el cambio ya está confirmado; solo se deja rastro en logs
        _log.error("auditoria_fallida", extra={"accion": accion, "error": type(exc).__name__})


async def sincronizar_calendario(
    pool: Any, sesion: SesionDashboard, fabrica: FabricaCalendario, cambio: CambioConfirmado
) -> dict[str, Any]:
    """Aplica el estado del turno a Calendar y persiste el id del evento. Nunca lanza: un fallo se registra."""
    fila = cambio.fila
    resultado = None
    motivo = None
    try:
        async with fabrica(ConexionBajoDemanda(lambda: abrir_tx(pool, sesion))) as servicio:
            resultado = await servicio.sincronizar_booking(
                t.booking_para_calendario(fila, cambio.estado, cambio.inicio, cambio.fin)
            )
    except Exception as exc:  # noqa: BLE001 - Calendar no configurado, token vencido, Google caído...
        motivo = type(exc).__name__
    if motivo is None and resultado is not None and resultado.event_id != fila["google_calendar_event_id"]:
        try:
            async with abrir_tx(pool, sesion) as conn:
                await conn.execute(t.SQL_GUARDAR_EVENTO, sesion.tenant_id, fila["id"], resultado.event_id)
        except Exception as exc:  # noqa: BLE001
            motivo = type(exc).__name__
    if motivo is not None:
        _log.error("calendar_sync_fallo", extra={"motivo": motivo})
        await auditar_aparte(
            pool, sesion, "calendar_sync_failed", fila["id"], {"estado": cambio.estado, "motivo": motivo}
        )
        return {"estado": "error", "evento": fila["google_calendar_event_id"]}
    assert resultado is not None  # noqa: S101 - estrechamiento de tipo para mypy: sin motivo de error hay resultado
    return {"estado": resultado.accion.value, "evento": resultado.event_id}


async def cerrar(
    pool: Any,
    sesion: SesionDashboard,
    tareas: Any,
    opciones: OpcionesAvisos,
    fabrica_calendario: FabricaCalendario,
    cambio: CambioConfirmado,
) -> dict[str, Any]:
    """Calendar, envío de los avisos ya encolados (el del turno y los de los pendientes liberados) y respuesta."""
    fila = cambio.fila
    calendario: dict[str, Any] = {"estado": "no_aplica"}
    evento = fila["google_calendar_event_id"]
    if cambio.sincronizar and (cambio.estado == "confirmed" or evento):
        sync = await sincronizar_calendario(pool, sesion, fabrica_calendario, cambio)
        evento = sync.pop("evento")
        calendario = sync
    aviso = "no_aplica"
    if cambio.plantilla is not None:
        aviso = await avisar(pool, sesion, tareas, opciones, fila, cambio.plantilla, cambio.inicio, cambio.aviso)
    await avisar_liberados(pool, sesion, tareas, opciones, cambio.liberados)
    actual = {
        **dict(fila),
        "status": cambio.estado,
        "start_at": cambio.inicio,
        "end_at": cambio.fin,
        "google_calendar_event_id": evento,
    }
    if cambio.estado != "pending":
        actual["pending_expires_at"] = None
    respuesta = t.presentar_turno(actual, fila["customer_deleted_at"] is not None)
    respuesta["calendario"] = calendario
    respuesta["aviso"] = aviso
    return respuesta
