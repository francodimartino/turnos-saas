"""Lecturas de las consultas del cliente (rol `bot_service`, solo select).

Todas filtran por `tenant_id` explícito además de la RLS; los turnos, además, por el `end_customer_id` del propio
cliente.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

LIMITE_TURNOS = 10
LIMITE_SERVICIOS = 40


@dataclass(frozen=True)
class TurnoProximo:
    inicio: datetime
    servicio: str
    profesional: str
    status: str  # 'pending' | 'confirmed'


@dataclass(frozen=True)
class ServicioPrecio:
    nombre: str
    precio: Decimal
    duracion_minutos: int


async def proximos_turnos(
    conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, ahora: datetime
) -> list[TurnoProximo]:
    """Turnos futuros (pendientes de aprobación o confirmados) del cliente, del más cercano al más lejano."""
    filas = await conn.fetch(
        "select lower(b.time_range) as inicio, s.name as servicio, p.name as profesional, b.status "
        "from public.bookings b "
        "join public.services s on s.tenant_id = b.tenant_id and s.id = b.service_id "
        "join public.professionals p on p.tenant_id = b.tenant_id and p.id = b.professional_id "
        "where b.tenant_id = $1 and b.end_customer_id = $2 and b.status in ('pending', 'confirmed') "
        "and lower(b.time_range) > $3 "
        "order by lower(b.time_range), b.id limit $4",
        tenant_id,
        end_customer_id,
        ahora,
        LIMITE_TURNOS,
    )
    return [TurnoProximo(f["inicio"], f["servicio"], f["profesional"], f["status"]) for f in filas]


async def servicios_activos(conn: Any, tenant_id: uuid.UUID) -> list[ServicioPrecio]:
    filas = await conn.fetch(
        "select name, price, duration_minutes from public.services "
        "where tenant_id = $1 and is_active order by name, id limit $2",
        tenant_id,
        LIMITE_SERVICIOS,
    )
    return [ServicioPrecio(f["name"], Decimal(f["price"]), f["duration_minutes"]) for f in filas]
