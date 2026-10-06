"""Acceso a datos de la reserva del bot (rol `bot_service`: select en catálogo, select/insert/update en bookings).

Todas las consultas filtran por `tenant_id` explícito además de la RLS (§1.7). Un servicio solo es reservable si
algún profesional activo lo ofrece en modo `auto` o `approval`: `deposit` no es elegible en el piloto (§7.1,
sin cobro real).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

MODOS_ELEGIBLES = ("auto", "approval")
HORAS_APROBACION_POR_DEFECTO = 24
_HORAS_MINIMO, _HORAS_MAXIMO = 1, 168  # el mismo rango que valida el trigger de `tenants.settings`

# SQLSTATE de `exclusion_violation`: lo levanta `bookings_sin_doble_booking`.
SQLSTATE_EXCLUSION = "23P01"

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


def calcular_vencimiento(ahora: datetime, inicio: datetime, horas: int) -> datetime:
    """`pending_expires_at` de un turno en `approval`: lo que venza antes entre el timeout y el inicio (§7.1)."""
    return min(ahora + timedelta(hours=horas), inicio)


def _es_doble_booking(error: Exception) -> bool:
    return getattr(error, "sqlstate", None) == SQLSTATE_EXCLUSION


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
) -> BookingCreado | None:
    """Inserta el turno: `confirmed` si no hay vencimiento, `pending` si lo hay.

    Devuelve `None` si el exclusion constraint lo rechazó (otro turno vigente del profesional se superpone).
    Corre dentro de un savepoint: la violación aborta solo el insert y la transacción del webhook (dedup,
    sesión) sigue sana. Se usa SQL de savepoint y no `conn.transaction()` porque la transacción la abrió
    `tenant_tx` con `BEGIN` explícito, que asyncpg no conoce.
    """
    estado = "confirmed" if pending_expires_at is None else "pending"
    await conn.execute("savepoint crear_booking")
    try:
        fila = await conn.fetchrow(
            _INSERTAR, tenant_id, end_customer_id, servicio_id, profesional_id, inicio, fin, estado, pending_expires_at
        )
    except Exception as error:
        await conn.execute("rollback to savepoint crear_booking")
        if _es_doble_booking(error):
            return None
        raise
    await conn.execute("release savepoint crear_booking")
    return BookingCreado(fila["id"], fila["status"], fila["pending_expires_at"])
