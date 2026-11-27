"""Cancelar y mover un turno por parte del cliente, y resolver un cambio de horario: una regla para todos los canales.

Quién la usa: el bot y la web de reservas (el cliente) y el panel del negocio (el que aprueba o rechaza). El dominio no
sabe de HTTP ni de ningún canal: recibe `canal` ('bot', 'web' o 'staff') solo para dejarlo en el `audit_log`, y levanta
errores propios que cada canal traduce a su respuesta.

Reglas:

- Cancelar y mover solo se puede hasta `cancelacion_plazo_horas` antes del inicio del turno (24 si el negocio no lo
  configuró; el valor justo en el plazo todavía se permite). Pasado el plazo: `PlazoVencido`. La regla vale igual para
  un turno `pending` (pedido todavía sin aprobar) que para uno `confirmed`: el cliente no tiene más margen por el
  hecho de que el negocio no haya contestado.
- Mover conserva la duración y el profesional. Con el servicio en modo `auto` (o con el turno todavía `pending`, que ya
  espera aprobación entera) el turno se mueve directo, con los mismos locks, el mismo choque y la misma anticipación
  mínima que el alta. Con el turno `confirmed` y el servicio en `approval` (o `deposit`, o sin asignación vigente: lo
  más cauteloso) no se toca el horario: queda el original reservado y el nuevo guardado como cambio pendiente, hasta
  que el negocio lo aprueba, lo rechaza o vence. Un solo cambio pendiente por turno; uno vencido que nadie revirtió
  todavía se descarta al volver a mover.
- Los límites de reserva que se aplican al mover son solo la anticipación mínima del horario nuevo: el tope de turnos
  activos y el de reservas por hora cuentan turnos creados, y mover no crea ninguno (aplicarlos bloquearía al cliente
  que está justo en el tope).
- Un turno que no es de ese cliente (o de ese tenant) responde `TurnoNoEncontrado`: nunca se distingue de uno que no
  existe.

Todo corre en un savepoint bajo el lock por (tenant, profesional) del alta; un choque con otro turno o con otra
propuesta (23P01, 40P01, 40001) vuelve como `HorarioOcupado`, nunca como excepción de base.
Cada escritura deja su fila en `audit_log` (actor y tenant salen del contexto de la transacción) en la misma
transacción.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from platform_audit import registrar
from turnos_queries.bloqueo import bloquear_profesional

from turnos_dominio import alta
from turnos_dominio import limites as lim
from turnos_dominio.limites import LimitesReserva, ReservaRechazada, interpretar
from turnos_dominio.settings_tenant import (
    CLAVE_HORAS_APROBACION,
    CLAVE_PLAZO_CANCELACION,
    PLAZO_CANCELACION_DEFECTO_HORAS,
)

UTC = timezone.utc  # noqa: UP017

CanalDelCambio = Literal["bot", "web", "staff"]
ResultadoMovimiento = Literal["movido", "pendiente_de_aprobacion"]
NombreSavepoint = Literal["cancelar_cliente", "mover_cliente", "resolver_cambio"]

REGLA_PLAZO_CANCELACION = (CLAVE_PLAZO_CANCELACION, 0, 720, PLAZO_CANCELACION_DEFECTO_HORAS)
REGLA_HORAS_APROBACION = (CLAVE_HORAS_APROBACION, 1, 168, 24)

SQLSTATE_CHECK = "23514"

SQL_PLAZO = "select settings ->> 'cancelacion_plazo_horas' from public.tenants where id = $1"
SQL_HORAS_APROBACION = "select settings ->> 'booking_approval_timeout_hours' from public.tenants where id = $1"

# `$3` nulo = sin filtrar por cliente (el staff). Sin asignación vigente del servicio al profesional: 'approval'.
SQL_CARGAR_TURNO = """
select b.id, b.status, b.end_customer_id, b.professional_id,
       lower(b.time_range) as inicio, upper(b.time_range) as fin, b.pending_expires_at,
       b.starts_at_propuesto, b.ends_at_propuesto, b.cambio_pendiente_expira_at,
       coalesce(ps.confirmation_mode, 'approval') as modo
from public.bookings b
left join public.professional_services ps
  on ps.tenant_id = b.tenant_id and ps.professional_id = b.professional_id and ps.service_id = b.service_id
where b.tenant_id = $1 and b.id = $2 and ($3::uuid is null or b.end_customer_id = $3::uuid)
"""
SQL_CARGAR_TURNO_BLOQUEANDO = SQL_CARGAR_TURNO + " for update of b"

SQL_CANCELAR = (
    "update public.bookings set status = 'cancelled' "
    "where tenant_id = $1 and id = $2 and status in ('pending', 'confirmed') returning id"
)

# Acortar el vencimiento al nuevo inicio: un pedido no puede quedar aprobable después de la hora del turno.
SQL_ACORTAR_VENCIMIENTO = (
    "update public.bookings set pending_expires_at = least(pending_expires_at, $3::timestamptz) "
    "where tenant_id = $1 and id = $2 and status = 'pending'"
)

SQL_PROPONER_CAMBIO = (
    "update public.bookings set starts_at_propuesto = $3::timestamptz, ends_at_propuesto = $4::timestamptz, "
    "cambio_pendiente_expira_at = $5::timestamptz "
    "where tenant_id = $1 and id = $2 and status = 'confirmed' and starts_at_propuesto is null returning id"
)

SQL_DESCARTAR_CAMBIO = (
    "update public.bookings set starts_at_propuesto = null, ends_at_propuesto = null, "
    "cambio_pendiente_expira_at = null "
    "where tenant_id = $1 and id = $2 and status = 'confirmed' and starts_at_propuesto is not null returning id"
)


class ErrorDeTurno(Exception):
    """Base de los errores de cancelar, mover y resolver cambios. El canal los traduce a su respuesta."""


class TurnoNoEncontrado(ErrorDeTurno):
    """No existe, es de otro tenant o de otro cliente."""


class TurnoNoModificable(ErrorDeTurno):
    """El turno ya está cancelado o completado, o es un pedido sin aprobar que ya venció."""


class PlazoVencido(ErrorDeTurno):
    """Faltan menos horas que el plazo del negocio: el cliente tiene que contactarlo."""

    def __init__(self, horas_plazo: int) -> None:
        super().__init__(f"plazo de {horas_plazo} horas vencido")
        self.horas_plazo = horas_plazo


class HorarioOcupado(ErrorDeTurno):
    """El horario nuevo choca con otro turno o con el cambio pendiente de otro."""


class HorarioSinCambios(ErrorDeTurno):
    """El horario pedido es el que el turno ya tiene."""


class CambioYaPendiente(ErrorDeTurno):
    """El turno ya espera la respuesta del negocio por otro cambio de horario."""


class CambioNoPendiente(ErrorDeTurno):
    """El turno no tiene un cambio de horario pendiente (o ya se resolvió)."""


class CambioVencido(ErrorDeTurno):
    """El cambio pendiente venció: no se puede aprobar (el horario original sigue vigente)."""


@dataclass(frozen=True)
class TurnoCancelado:
    id: uuid.UUID
    inicio: datetime
    fin: datetime
    estado_previo: str


@dataclass(frozen=True)
class TurnoMovido:
    id: uuid.UUID
    resultado: ResultadoMovimiento
    inicio: datetime  # el horario vigente del turno (el original si quedó pendiente de aprobación)
    fin: datetime
    inicio_propuesto: datetime | None = None
    fin_propuesto: datetime | None = None
    cambio_pendiente_expira_at: datetime | None = None


@dataclass(frozen=True)
class CambioResuelto:
    id: uuid.UUID
    inicio: datetime  # el horario que quedó vigente
    fin: datetime
    aprobado: bool


async def plazo_cancelacion_horas(conn: Any, tenant_id: uuid.UUID) -> int:
    """`cancelacion_plazo_horas` del tenant (0 a 720), o 24 si no está o no es un entero válido."""
    return interpretar(await conn.fetchval(SQL_PLAZO, tenant_id), REGLA_PLAZO_CANCELACION)


async def horas_aprobacion_cambio(conn: Any, tenant_id: uuid.UUID) -> int:
    """`booking_approval_timeout_hours` del tenant (1 a 168), o 24 si no está o no es un entero válido."""
    return interpretar(await conn.fetchval(SQL_HORAS_APROBACION, tenant_id), REGLA_HORAS_APROBACION)


def exigir_plazo(inicio: datetime, ahora: datetime, horas_plazo: int) -> None:
    """Levanta `PlazoVencido` si faltan menos de `horas_plazo` horas. Justo en el plazo todavía se permite."""
    if inicio - ahora < timedelta(hours=horas_plazo):
        raise PlazoVencido(horas_plazo)


@asynccontextmanager
async def _en_savepoint(conn: Any, nombre: NombreSavepoint) -> AsyncIterator[None]:
    await conn.execute(f"savepoint {nombre}")
    try:
        yield
    except Exception as error:
        await conn.execute(f"rollback to savepoint {nombre}")
        if alta.es_choque(error):
            raise HorarioOcupado(type(error).__name__) from error
        raise
    await conn.execute(f"release savepoint {nombre}")


async def _cargar_bloqueado(
    conn: Any, tenant_id: uuid.UUID, booking_id: uuid.UUID, end_customer_id: uuid.UUID | None
) -> Any:
    """Lee el turno, toma el lock del profesional (antes que la fila, como el alta) y lo vuelve a leer bajo lock."""
    previa = await conn.fetchrow(SQL_CARGAR_TURNO, tenant_id, booking_id, end_customer_id)
    if previa is None:
        raise TurnoNoEncontrado(str(booking_id))
    await bloquear_profesional(conn, tenant_id=tenant_id, profesional_id=previa["professional_id"])
    fila = await conn.fetchrow(SQL_CARGAR_TURNO_BLOQUEANDO, tenant_id, booking_id, end_customer_id)
    if fila is None:
        raise TurnoNoEncontrado(str(booking_id))
    return fila


def _exigir_modificable(fila: Any, ahora: datetime) -> None:
    if fila["status"] not in ("pending", "confirmed"):
        raise TurnoNoModificable(fila["status"])
    vencimiento = fila["pending_expires_at"]
    if fila["status"] == "pending" and vencimiento is not None and vencimiento <= ahora:
        raise TurnoNoModificable("pending_vencido")


async def _auditar(
    conn: Any, accion: str, booking_id: uuid.UUID, canal: CanalDelCambio, **fechas: datetime | None
) -> None:
    metadata: dict[str, Any] = {"canal": canal}
    metadata.update({clave: valor.isoformat() for clave, valor in fechas.items() if valor is not None})
    await registrar(conn, accion, "booking", booking_id, metadata)


async def cancelar_turno_cliente(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    end_customer_id: uuid.UUID,
    booking_id: uuid.UUID,
    canal: CanalDelCambio,
    ahora: datetime | None = None,
) -> TurnoCancelado:
    """Cancela el turno del cliente si falta al menos el plazo del negocio (`PlazoVencido` si no)."""
    ahora = ahora or datetime.now(UTC)
    async with _en_savepoint(conn, "cancelar_cliente"):
        fila = await _cargar_bloqueado(conn, tenant_id, booking_id, end_customer_id)
        _exigir_modificable(fila, ahora)
        exigir_plazo(fila["inicio"], ahora, await plazo_cancelacion_horas(conn, tenant_id))
        if await conn.fetchrow(SQL_CANCELAR, tenant_id, booking_id) is None:
            raise TurnoNoModificable(fila["status"])
        await _auditar(conn, "booking_cancelled_by_customer", booking_id, canal, inicio=fila["inicio"])
    return TurnoCancelado(booking_id, fila["inicio"], fila["fin"], fila["status"])


async def mover_turno_cliente(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    end_customer_id: uuid.UUID,
    booking_id: uuid.UUID,
    nuevo_inicio: datetime,
    canal: CanalDelCambio,
    limites: LimitesReserva | None = None,
    ahora: datetime | None = None,
) -> TurnoMovido:
    """Mueve el turno del cliente a `nuevo_inicio` (misma duración y profesional), directo o pidiendo aprobación.

    `limites` (regla opcional del canal): si se pasa, el horario nuevo tiene que respetar la anticipación mínima
    (`ReservaRechazada`). Ver el docstring del módulo para el resto de las reglas.
    """
    ahora = ahora or datetime.now(UTC)
    async with _en_savepoint(conn, "mover_cliente"):
        fila = await _cargar_bloqueado(conn, tenant_id, booking_id, end_customer_id)
        _exigir_modificable(fila, ahora)
        exigir_plazo(fila["inicio"], ahora, await plazo_cancelacion_horas(conn, tenant_id))
        if nuevo_inicio == fila["inicio"]:
            raise HorarioSinCambios(str(booking_id))
        if limites is not None and nuevo_inicio < ahora + timedelta(minutes=limites.anticipacion_minutos):
            raise ReservaRechazada(lim.MOTIVO_ANTICIPACION)
        nuevo_fin = nuevo_inicio + (fila["fin"] - fila["inicio"])
        if fila["starts_at_propuesto"] is not None:
            if fila["cambio_pendiente_expira_at"] > ahora:
                raise CambioYaPendiente(str(booking_id))
            # vencido y sin revertir todavía: vuelve el original (la base no deja mover mientras siga la propuesta)
            await conn.fetchrow(SQL_DESCARTAR_CAMBIO, tenant_id, booking_id)
        if fila["status"] == "confirmed" and fila["modo"] != "auto":
            movido = await _proponer(conn, tenant_id, booking_id, fila, nuevo_inicio, nuevo_fin, ahora)
            await _auditar(
                conn, "booking_move_requested", booking_id, canal, inicio=fila["inicio"], inicio_propuesto=nuevo_inicio
            )
            return movido
        resultado = await alta.mover_turno(
            conn,
            tenant_id=tenant_id,
            booking_id=booking_id,
            profesional_id=fila["professional_id"],
            inicio=nuevo_inicio,
            fin=nuevo_fin,
        )
        if resultado == "choque":
            raise HorarioOcupado(str(booking_id))
        if resultado == "no_existe":
            raise TurnoNoEncontrado(str(booking_id))
        if fila["status"] == "pending":
            await conn.execute(SQL_ACORTAR_VENCIMIENTO, tenant_id, booking_id, nuevo_inicio)
        await _auditar(
            conn, "booking_moved_by_customer", booking_id, canal, inicio=fila["inicio"], inicio_nuevo=nuevo_inicio
        )
    return TurnoMovido(booking_id, "movido", nuevo_inicio, nuevo_fin)


async def _proponer(
    conn: Any,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    fila: Any,
    nuevo_inicio: datetime,
    nuevo_fin: datetime,
    ahora: datetime,
) -> TurnoMovido:
    horas = await horas_aprobacion_cambio(conn, tenant_id)
    vence = min(ahora + timedelta(hours=horas), fila["inicio"])
    if await conn.fetchrow(SQL_PROPONER_CAMBIO, tenant_id, booking_id, nuevo_inicio, nuevo_fin, vence) is None:
        raise CambioYaPendiente(str(booking_id))
    return TurnoMovido(
        booking_id, "pendiente_de_aprobacion", fila["inicio"], fila["fin"], nuevo_inicio, nuevo_fin, vence
    )


async def aprobar_cambio_horario(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    canal: CanalDelCambio = "staff",
    ahora: datetime | None = None,
) -> CambioResuelto:
    """El negocio acepta el cambio: el horario propuesto pasa a ser el del turno. Un cambio vencido no se aprueba."""
    ahora = ahora or datetime.now(UTC)
    async with _en_savepoint(conn, "resolver_cambio"):
        fila = await _cargar_bloqueado(conn, tenant_id, booking_id, None)
        if fila["status"] != "confirmed" or fila["starts_at_propuesto"] is None:
            raise CambioNoPendiente(str(booking_id))
        if fila["cambio_pendiente_expira_at"] <= ahora:
            raise CambioVencido(str(booking_id))
        try:
            aplicado = await alta.aplicar_cambio_pendiente(conn, tenant_id=tenant_id, booking_id=booking_id)
        except Exception as error:
            if getattr(error, "sqlstate", None) == SQLSTATE_CHECK:
                raise CambioVencido(str(booking_id)) from error
            raise
        if not aplicado:
            raise CambioNoPendiente(str(booking_id))
        await _auditar(
            conn,
            "booking_change_approved",
            booking_id,
            canal,
            inicio=fila["inicio"],
            inicio_propuesto=fila["starts_at_propuesto"],
        )
    return CambioResuelto(booking_id, fila["starts_at_propuesto"], fila["ends_at_propuesto"], True)


async def rechazar_cambio_horario(
    conn: Any, *, tenant_id: uuid.UUID, booking_id: uuid.UUID, canal: CanalDelCambio = "staff"
) -> CambioResuelto:
    """El negocio rechaza el cambio: el turno conserva su horario original. Sirve también si ya venció."""
    async with _en_savepoint(conn, "resolver_cambio"):
        fila = await _cargar_bloqueado(conn, tenant_id, booking_id, None)
        if fila["status"] != "confirmed" or fila["starts_at_propuesto"] is None:
            raise CambioNoPendiente(str(booking_id))
        if await conn.fetchrow(SQL_DESCARTAR_CAMBIO, tenant_id, booking_id) is None:
            raise CambioNoPendiente(str(booking_id))
        await _auditar(
            conn,
            "booking_change_rejected",
            booking_id,
            canal,
            inicio=fila["inicio"],
            inicio_propuesto=fila["starts_at_propuesto"],
        )
    return CambioResuelto(booking_id, fila["inicio"], fila["fin"], False)
