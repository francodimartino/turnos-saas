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

from turnos_dominio import alta
from turnos_dominio.alta import Liberado as Liberado  # reexportado: lo usa el aviso de liberados
from turnos_dominio.alta import TurnoCreado

from turnos_bot.reserva import limites as lim
from turnos_bot.reserva.limites import LimitesReserva

MODOS_ELEGIBLES = ("auto", "approval")
HORAS_APROBACION_POR_DEFECTO = 24
_HORAS_MINIMO, _HORAS_MAXIMO = 1, 168  # el mismo rango que valida el trigger de `tenants.settings`

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


# El resultado del alta es el del dominio; el flujo del bot lo conoce por este nombre.
BookingCreado = TurnoCreado


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
    """Crea el turno del bot: `confirmed` si no hay vencimiento, `pending` si lo hay.

    Delega en `turnos_dominio.alta.crear_turno` (savepoint, locks, límites, liberación e insert; ver ese
    módulo). Lo que decide el bot: el estado sale del vencimiento, libera los `pending` vencidos que se
    solapan (los liberados vuelven en `BookingCreado.liberados` para avisarles después del commit) y aplica
    `limites` si se los pasan (`ReservaRechazada` si alguno se excede, sin insertar). Devuelve `None` si el
    horario ya no se pudo tomar (choque).
    """
    return await alta.crear_turno(
        conn,
        tenant_id=tenant_id,
        end_customer_id=end_customer_id,
        servicio_id=servicio_id,
        profesional_id=profesional_id,
        inicio=inicio,
        fin=fin,
        estado="confirmed" if pending_expires_at is None else "pending",
        pending_expires_at=pending_expires_at,
        limites=limites,
        liberar_vencidos=True,
        ahora=ahora,
    )
