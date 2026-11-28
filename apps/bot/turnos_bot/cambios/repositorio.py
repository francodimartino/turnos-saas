"""Lectura del turno propio del cliente para cancelarlo o moverlo (rol `bot_service`, solo select).

Filtra por `tenant_id` explícito, además de la RLS, y por el `end_customer_id` de quien escribe: un turno de otra
persona o de otro negocio es indistinguible de uno que no existe. La decisión de si se puede cancelar o mover
(plazo, estado, horario) es del dominio (`turnos_dominio.cambios`), no de esta consulta.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SQL_TURNO_PROPIO = """
select b.id, b.status, lower(b.time_range) as inicio, upper(b.time_range) as fin,
       b.service_id, b.professional_id, s.name as servicio, p.name as profesional
from public.bookings b
join public.services s on s.tenant_id = b.tenant_id and s.id = b.service_id
join public.professionals p on p.tenant_id = b.tenant_id and p.id = b.professional_id
where b.tenant_id = $1 and b.id = $2 and b.end_customer_id = $3
"""

ESTADOS_MODIFICABLES = ("pending", "confirmed")


@dataclass(frozen=True)
class TurnoPropio:
    id: uuid.UUID
    estado: str
    inicio: datetime
    fin: datetime
    servicio_id: uuid.UUID
    profesional_id: uuid.UUID
    servicio: str
    profesional: str

    @property
    def modificable(self) -> bool:
        """Pendiente o confirmado: el único estado en que tiene sentido ofrecerle cancelar o mover."""
        return self.estado in ESTADOS_MODIFICABLES


async def turno_propio(
    conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, booking_id: uuid.UUID
) -> TurnoPropio | None:
    """El turno si es de esa persona en ese negocio; `None` si no existe, es de otra persona o de otro negocio."""
    fila = await conn.fetchrow(SQL_TURNO_PROPIO, tenant_id, booking_id, end_customer_id)
    if fila is None:
        return None
    return TurnoPropio(
        fila["id"],
        fila["status"],
        fila["inicio"],
        fila["fin"],
        fila["service_id"],
        fila["professional_id"],
        fila["servicio"],
        fila["profesional"],
    )
