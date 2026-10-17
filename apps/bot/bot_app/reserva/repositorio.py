"""Acceso a datos de la reserva del bot (rol `bot_service`: select en catálogo, select/insert/update en bookings).

Todas las consultas filtran por `tenant_id` explícito además de la RLS (§1.7). Un servicio solo es reservable si
algún profesional activo lo ofrece en modo `auto` o `approval`: `deposit` no es elegible en el piloto (§7.1,
sin cobro real).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from turnos_queries.bloqueo import bloquear_profesional

from bot_app.reserva import limites as lim
from bot_app.reserva.limites import LimitesReserva, ReservaRechazada

UTC = timezone.utc  # noqa: UP017
MODOS_ELEGIBLES = ("auto", "approval")
HORAS_APROBACION_POR_DEFECTO = 24
_HORAS_MINIMO, _HORAS_MAXIMO = 1, 168  # el mismo rango que valida el trigger de `tenants.settings`

# SQLSTATE de `exclusion_violation`: lo levanta `bookings_sin_doble_booking`.
SQLSTATE_EXCLUSION = "23P01"
# Deadlock y fallo de serialización: con el lock por profesional no deberían darse; si pasan, es otra reserva
# compitiendo por el mismo horario y se trata igual que un choque.
SQLSTATES_CONCURRENCIA = ("40P01", "40001")

_SERVICIOS = """
select s.id, s.name, s.duration_minutes, s.price
from public.services s
where s.tenant_id = $1 and s.is_active
  and exists (
    select 1 from public.professional_services ps
    join public.professionals p on p.id = ps.professional_id
    where ps.service_id = s.id and ps.tenant_id = $1 and p.tenant_id = $1 and p.is_active
      and ps.confirmation_mode = any($2::text[]))
order by s.name, s.id
"""

_PROFESIONALES = """
select p.id, p.name
from public.professionals p
join public.professional_services ps on ps.professional_id = p.id
where p.tenant_id = $1 and ps.tenant_id = $1 and ps.service_id = $3 and p.is_active
  and ps.confirmation_mode = any($2::text[])
order by p.name, p.id
"""

_DATOS_RESERVA = """
select s.name as servicio, p.name as profesional, s.duration_minutes, ps.confirmation_mode as modo
from public.professional_services ps
join public.professionals p on p.id = ps.professional_id
join public.services s on s.id = ps.service_id
where ps.tenant_id = $1 and p.tenant_id = $1 and s.tenant_id = $1
  and ps.professional_id = $2 and ps.service_id = $3
  and p.is_active and s.is_active and ps.confirmation_mode = any($4::text[])
"""

_HORAS_APROBACION = "select settings ->> 'booking_approval_timeout_hours' from public.tenants where id = $1"

_LIMITES = """
select settings ->> 'booking_max_active_per_customer', settings ->> 'booking_max_per_hour',
       settings ->> 'booking_min_notice_minutes'
from public.tenants where id = $1
"""

# Lock por (tenant, cliente final): serializa las reservas de un mismo cliente entre profesionales distintos, que
# el lock por profesional no cubre. Orden fijo (cliente, después profesional); el dashboard solo toma el del
# profesional, así que no hay ciclo posible.
_BLOQUEAR_CLIENTE = (
    "select pg_advisory_xact_lock(hashtextextended('turnos_cliente:' || $1::text || ':' || $2::text, 0))"
)

# Turnos futuros activos del cliente (pending o confirmed) y reservas que creó en la última hora (cualquier
# estado: cancelar y volver a reservar no esquiva el límite por hora).
_CONTAR = """
select
  count(*) filter (where status in ('pending', 'confirmed') and lower(time_range) > $3),
  count(*) filter (where created_at > $4)
from public.bookings
where tenant_id = $1 and end_customer_id = $2
"""

_INSERTAR = """
insert into public.bookings
  (tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at)
values ($1, $2, $3, $4, tstzrange($5::timestamptz, $6::timestamptz, '[)'), $7, $8::timestamptz)
returning id, status, pending_expires_at
"""


@dataclass(frozen=True)
class Servicio:
    id: uuid.UUID
    nombre: str
    duracion_minutos: int
    precio: Decimal


@dataclass(frozen=True)
class Profesional:
    id: uuid.UUID
    nombre: str


@dataclass(frozen=True)
class DatosReserva:
    servicio: str
    profesional: str
    duracion_minutos: int
    modo: str  # 'auto' | 'approval'


@dataclass(frozen=True)
class BookingCreado:
    id: uuid.UUID
    status: str  # 'confirmed' | 'pending'
    pending_expires_at: datetime | None


async def listar_servicios(conn: Any, tenant_id: uuid.UUID) -> list[Servicio]:
    filas = await conn.fetch(_SERVICIOS, tenant_id, list(MODOS_ELEGIBLES))
    return [Servicio(f["id"], f["name"], f["duration_minutes"], f["price"]) for f in filas]


async def listar_profesionales(conn: Any, tenant_id: uuid.UUID, servicio_id: uuid.UUID) -> list[Profesional]:
    filas = await conn.fetch(_PROFESIONALES, tenant_id, list(MODOS_ELEGIBLES), servicio_id)
    return [Profesional(f["id"], f["name"]) for f in filas]


async def datos_reserva(
    conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID, servicio_id: uuid.UUID
) -> DatosReserva | None:
    """Datos vigentes de la pareja profesional-servicio, o `None` si ya no es reservable por el bot."""
    fila = await conn.fetchrow(_DATOS_RESERVA, tenant_id, profesional_id, servicio_id, list(MODOS_ELEGIBLES))
    if fila is None:
        return None
    return DatosReserva(fila["servicio"], fila["profesional"], fila["duration_minutes"], fila["modo"])


async def horas_aprobacion(conn: Any, tenant_id: uuid.UUID) -> int:
    """`booking_approval_timeout_hours` del tenant (1 a 168), o 24 si no está o no es un entero válido."""
    valor = await conn.fetchval(_HORAS_APROBACION, tenant_id)
    if isinstance(valor, str) and re.fullmatch(r"\d{1,4}", valor):
        horas = int(valor)
        if _HORAS_MINIMO <= horas <= _HORAS_MAXIMO:
            return horas
    return HORAS_APROBACION_POR_DEFECTO


async def limites_reserva(conn: Any, tenant_id: uuid.UUID) -> LimitesReserva:
    """Límites del tenant (settings) con el default del bot donde la clave falta o no es un entero válido."""
    fila = await conn.fetchrow(_LIMITES, tenant_id)
    if fila is None:
        return LimitesReserva()
    activos, por_hora, aviso = fila[0], fila[1], fila[2]
    return LimitesReserva(
        lim.interpretar(activos, lim.MAX_ACTIVOS_POR_CLIENTE),
        lim.interpretar(por_hora, lim.MAX_POR_HORA),
        lim.interpretar(aviso, lim.ANTICIPACION_MINIMA_MINUTOS),
    )


def calcular_vencimiento(ahora: datetime, inicio: datetime, horas: int) -> datetime:
    """`pending_expires_at` de un turno en `approval`: lo que venza antes entre el timeout y el inicio (§7.1)."""
    return min(ahora + timedelta(hours=horas), inicio)


def _es_choque(error: Exception) -> bool:
    return getattr(error, "sqlstate", None) in (SQLSTATE_EXCLUSION, *SQLSTATES_CONCURRENCIA)


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
    fila = await conn.fetchrow(_CONTAR, tenant_id, end_customer_id, ahora, desde_hora)
    if fila[0] >= limites.max_activos_por_cliente:
        raise ReservaRechazada(lim.MOTIVO_TOPE_ACTIVOS)
    if fila[1] >= limites.max_por_hora:
        raise ReservaRechazada(lim.MOTIVO_LIMITE_POR_HORA)


async def crear_booking(
    conn: Any,
    *,
    tenant_id: uuid.UUID,
    end_customer_id: uuid.UUID,
    servicio_id: uuid.UUID,
    profesional_id: uuid.UUID,
    inicio: datetime,
    fin: datetime,
    pending_expires_at: datetime | None,
    limites: LimitesReserva | None = None,
    ahora: datetime | None = None,
) -> BookingCreado | None:
    """Inserta el turno: `confirmed` si no hay vencimiento, `pending` si lo hay.

    Devuelve `None` si el turno no se pudo tomar: el exclusion constraint lo rechazó (otro turno vigente del
    profesional se superpone) o Postgres abortó por deadlock/serialización (40P01/40001).
    Corre dentro de un savepoint: el error aborta solo este intento y la transacción del webhook (dedup,
    sesión) sigue sana. Se usa SQL de savepoint y no `conn.transaction()` porque la transacción la abrió
    `tenant_tx` con `BEGIN` explícito, que asyncpg no conoce.

    Antes del insert toma el mismo lock por (tenant, profesional) que el dashboard, así las dos puertas de
    entrada se serializan y el choque llega limpio (23P01). Ante un 40P01/40001 residual no se reintenta la
    transacción del webhook (el dedup y la sesión van en la misma, repetirla no es trivial): se vuelve al
    savepoint y el cliente recibe "ya no está disponible" con horarios nuevos. El lock va dentro del savepoint
    para que un deadlock al tomarlo también se resuelva así; al hacer `release` se conserva hasta el commit.

    Con `limites` (el bot), y bajo los locks del cliente y del profesional, revalida en la misma transacción
    que inserta: anticipación mínima, tope de turnos futuros activos y reservas de la última hora. Si alguno
    se excede levanta `ReservaRechazada` (sin insertar). Sin `limites` (staff) no se aplica ninguno.
    """
    estado = "confirmed" if pending_expires_at is None else "pending"
    await conn.execute("savepoint crear_booking")
    try:
        if limites is not None:
            await conn.execute(_BLOQUEAR_CLIENTE, str(tenant_id), str(end_customer_id))
        await bloquear_profesional(conn, tenant_id=tenant_id, profesional_id=profesional_id)
        if limites is not None:
            await _exigir_limites(conn, tenant_id, end_customer_id, inicio, limites, ahora or datetime.now(UTC))
        fila = await conn.fetchrow(
            _INSERTAR, tenant_id, end_customer_id, servicio_id, profesional_id, inicio, fin, estado, pending_expires_at
        )
    except Exception as error:
        await conn.execute("rollback to savepoint crear_booking")
        if isinstance(error, ReservaRechazada):
            raise
        if _es_choque(error):
            return None
        raise
    await conn.execute("release savepoint crear_booking")
    return BookingCreado(fila["id"], fila["status"], fila["pending_expires_at"])
