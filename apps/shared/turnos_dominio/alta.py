"""Alta y movimiento de turnos: la escritura de `bookings` que comparten todos los canales.

Dos funciones, pensadas para el bot y para el panel del dueño:

- `crear_turno(conn, ...)`: un solo INSERT de `bookings`. Lo que decide cada canal entra como parámetro
  explícito y nada se asume:
    * `estado` y `pending_expires_at`: `confirmed` sin vencimiento, o `pending` con él (el bot en modo
      aprobación). El dominio solo valida que sean coherentes.
    * `booking_pack_id`: el pack del que consume el turno (el panel); el bot no lo usa.
    * `limites` (regla opcional): con `LimitesReserva` revalida, en la misma transacción que inserta,
      la anticipación mínima, el tope de turnos futuros activos del cliente y las reservas de la última hora.
      El bot la activa; el staff no la pasa y no se aplica ninguna.
    * `liberar_vencidos` (regla opcional): antes de insertar cancela los `pending` vencidos que se solapan
      (`release_expired_pending_overlaps`, solo permitida al rol del bot); los liberados vuelven en
      `TurnoCreado.liberados` para que el canal avise después del commit.
- `mover_turno(conn, ...)`: un solo UPDATE de `time_range` del mismo turno.

Contrato común: `conn` viene de `platform_tenancy.tenant_tx` (la transacción la abre el canal, el dominio no
la abre ni la cierra). Orden fijo: savepoint, lock por cliente (solo con `limites`), lock por (tenant,
profesional), límites, liberación, escritura. Todo corre dentro de un savepoint: un choque con el exclusion
constraint (23P01) o un deadlock/serialización residual (40P01/40001) no aborta la transacción del canal y se
devuelve como resultado (`None` en el alta, `"choque"` al mover), no como excepción. Cualquier otro error se
propaga tras volver al savepoint. Un límite excedido levanta `ReservaRechazada` sin insertar.

Las sentencias filtran por `tenant_id` explícito además de la RLS. No hay lógica de canal acá: qué estado
inicial, qué límites y qué se hace con los liberados lo decide quien llama. Los avisos (outbox, mensajes) tampoco
viven acá: el canal los encola en la misma transacción después de que el alta devuelve el turno.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from turnos_queries.bloqueo import bloquear_profesional

from turnos_dominio import limites as lim
from turnos_dominio.limites import LimitesReserva, ReservaRechazada

UTC = timezone.utc  # noqa: UP017

# SQLSTATE de `exclusion_violation`: lo levanta `bookings_sin_doble_booking`.
SQLSTATE_EXCLUSION = "23P01"
# Deadlock y fallo de serialización: con el lock por profesional no deberían darse; si pasan, es otra reserva
# compitiendo por el mismo horario y se trata igual que un choque.
SQLSTATES_CONCURRENCIA = ("40P01", "40001")

EstadoInicial = Literal["confirmed", "pending"]
ResultadoMovimiento = Literal["movido", "choque", "no_existe"]

# Lock por (tenant, cliente final): serializa las reservas de un mismo cliente entre profesionales distintos, que
# el lock por profesional no cubre. Orden fijo (cliente, después profesional); quien no pasa `limites` solo
# toma el del profesional, así que no hay ciclo posible.
SQL_BLOQUEAR_CLIENTE = (
    "select pg_advisory_xact_lock(hashtextextended('turnos_cliente:' || $1::text || ':' || $2::text, 0))"
)

# Turnos futuros activos del cliente (pending o confirmed) y reservas que creó en la última hora (cualquier
# estado: cancelar y volver a reservar no esquiva el límite por hora).
SQL_CONTAR_RESERVAS = """
select
  count(*) filter (where status in ('pending', 'confirmed') and lower(time_range) > $3),
  count(*) filter (where created_at > $4)
from public.bookings
where tenant_id = $1 and end_customer_id = $2
"""

SQL_INSERTAR_TURNO = """
insert into public.bookings
  (tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at,
   booking_pack_id)
values ($1, $2, $3, $4, tstzrange($5::timestamptz, $6::timestamptz, '[)'), $7, $8::timestamptz, $9)
returning id, status, pending_expires_at
"""

# Mover = actualizar el rango del mismo turno; un choque lo rechaza el exclusion constraint (23P01).
SQL_MOVER_TURNO = (
    "update public.bookings set time_range = tstzrange($3::timestamptz, $4::timestamptz, '[)') "
    "where tenant_id = $1 and id = $2 returning id"
)

# Libera los pending ya vencidos que solapan con el rango (solo bot_service; toma el mismo lock por profesional,
# re-entrante). Devuelve a quién hay que avisar.
SQL_LIBERAR_VENCIDOS = (
    "select * from public.release_expired_pending_overlaps($1, tstzrange($2::timestamptz, $3::timestamptz, '[)'))"
)


@dataclass(frozen=True)
class Liberado:
    """Un pending vencido cancelado para dejar reservar su horario; `notify` dice si hay a quién avisar."""

    tenant_id: uuid.UUID
    booking_id: uuid.UUID
    channel: str | None = field(default=None, repr=False)
    channel_user_id: str | None = field(default=None, repr=False)
    notify: bool = False
    end_customer_id: uuid.UUID | None = None
    starts_at: datetime | None = None  # inicio del turno vencido (para decir cuál venció)
    service_name: str | None = None


@dataclass(frozen=True)
class TurnoCreado:
    id: uuid.UUID
    status: str  # 'confirmed' | 'pending'
    pending_expires_at: datetime | None
    liberados: tuple[Liberado, ...] = ()  # pending vencidos cancelados para tomar este horario (a avisar)


def es_choque(error: Exception) -> bool:
    """El horario ya no está libre: exclusion constraint, o deadlock/serialización contra otra reserva."""
    return getattr(error, "sqlstate", None) in (SQLSTATE_EXCLUSION, *SQLSTATES_CONCURRENCIA)


def _validar_estado(estado: str, pending_expires_at: datetime | None) -> None:
    if estado == "confirmed" and pending_expires_at is None:
        return
    if estado == "pending" and pending_expires_at is not None:
        return
    raise ValueError("el estado inicial es 'confirmed' sin vencimiento o 'pending' con vencimiento")


async def _exigir_limites(
    conn: Any,
    tenant_id: uuid.UUID,
    end_customer_id: uuid.UUID,
    inicio: datetime,
    limites: LimitesReserva,
    ahora: datetime,
) -> None:
    if inicio < ahora + timedelta(minutes=limites.anticipacion_minutos):
        raise ReservaRechazada(lim.MOTIVO_ANTICIPACION)
    desde_hora = ahora - timedelta(minutes=lim.VENTANA_POR_HORA_MINUTOS)
    fila = await conn.fetchrow(SQL_CONTAR_RESERVAS, tenant_id, end_customer_id, ahora, desde_hora)
    if fila[0] >= limites.max_activos_por_cliente:
        raise ReservaRechazada(lim.MOTIVO_TOPE_ACTIVOS)
    if fila[1] >= limites.max_por_hora:
        raise ReservaRechazada(lim.MOTIVO_LIMITE_POR_HORA)


async def crear_turno(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    end_customer_id: uuid.UUID,
    servicio_id: uuid.UUID,
    profesional_id: uuid.UUID,
    inicio: datetime,
    fin: datetime,
    estado: EstadoInicial,
    pending_expires_at: datetime | None = None,
    booking_pack_id: uuid.UUID | None = None,
    limites: LimitesReserva | None = None,
    liberar_vencidos: bool = False,
    ahora: datetime | None = None,
) -> TurnoCreado | None:
    """Inserta el turno con el `estado` y el vencimiento que decide el canal.

    Devuelve `None` si el horario no se pudo tomar (choque: ver el docstring del módulo). Ante un 40P01/40001
    residual no se reintenta la transacción del canal (puede llevar dedup y sesión, repetirla no es trivial): se
    vuelve al savepoint y el canal le dice al cliente que el horario ya no está. El lock va dentro del
    savepoint para que un deadlock al tomarlo se resuelva igual; al hacer `release` se conserva hasta el commit.

    `limites` (regla opcional del canal): bajo los locks del cliente y del profesional revalida anticipación,
    tope de activos y reservas por hora; si alguno se excede levanta `ReservaRechazada` sin insertar.
    `liberar_vencidos` (regla opcional): con el lock ya tomado y justo antes del insert libera los `pending`
    vencidos que se solapan, porque para el exclusion constraint siguen ocupando hasta que el job los cancela;
    el rollback al savepoint deshace también esa liberación. Un pendiente vigente no se toca.
    """
    _validar_estado(estado, pending_expires_at)
    await conn.execute("savepoint crear_turno")
    try:
        if limites is not None:
            await conn.execute(SQL_BLOQUEAR_CLIENTE, str(tenant_id), str(end_customer_id))
        await bloquear_profesional(conn, tenant_id=tenant_id, profesional_id=profesional_id)
        if limites is not None:
            await _exigir_limites(conn, tenant_id, end_customer_id, inicio, limites, ahora or datetime.now(UTC))
        liberados: tuple[Liberado, ...] = ()
        if liberar_vencidos:
            liberados = tuple(
                Liberado(
                    f["tenant_id"],
                    f["booking_id"],
                    f["channel"],
                    f["channel_user_id"],
                    f["notify"],
                    f["end_customer_id"],
                    f["starts_at"],
                    f["service_name"],
                )
                for f in await conn.fetch(SQL_LIBERAR_VENCIDOS, profesional_id, inicio, fin)
            )
        fila = await conn.fetchrow(
            SQL_INSERTAR_TURNO,
            tenant_id,
            end_customer_id,
            servicio_id,
            profesional_id,
            inicio,
            fin,
            estado,
            pending_expires_at,
            booking_pack_id,
        )
    except Exception as error:
        await conn.execute("rollback to savepoint crear_turno")
        if isinstance(error, ReservaRechazada):
            raise
        if es_choque(error):
            return None
        raise
    await conn.execute("release savepoint crear_turno")
    return TurnoCreado(fila["id"], fila["status"], fila["pending_expires_at"], liberados)


async def mover_turno(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    profesional_id: uuid.UUID,
    inicio: datetime,
    fin: datetime,
) -> ResultadoMovimiento:
    """Cambia el rango del turno `booking_id` (del profesional `profesional_id`).

    Toma el mismo lock por (tenant, profesional) que el alta y hace un solo UPDATE de `time_range`.
    Devuelve `"movido"`, `"choque"` (el horario nuevo está ocupado) o `"no_existe"` (no hay un turno con ese id
    en el tenant). Las transiciones de estado y las reglas de qué turnos se pueden mover las decide el canal.
    """
    await conn.execute("savepoint mover_turno")
    try:
        await bloquear_profesional(conn, tenant_id=tenant_id, profesional_id=profesional_id)
        fila = await conn.fetchrow(SQL_MOVER_TURNO, tenant_id, booking_id, inicio, fin)
    except Exception as error:
        await conn.execute("rollback to savepoint mover_turno")
        if es_choque(error):
            return "choque"
        raise
    await conn.execute("release savepoint mover_turno")
    return "no_existe" if fila is None else "movido"
