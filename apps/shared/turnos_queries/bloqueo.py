"""Lock por (tenant, profesional) que serializa las altas y movimientos de turnos (ARCHITECTURE.md §7.1).

Lo toman por igual el dashboard-backend y el bot: la clave tiene que ser exactamente la misma o no se serializan
entre sí. Por eso vive acá y no en ninguna de las dos apps.

Sin el lock, dos transacciones que insertan turnos solapados chocan en el exclusion constraint
(`bookings_sin_doble_booking`): cada una inserta su fila y después espera a la otra al validar el índice, y
Postgres aborta una con deadlock (40P01) de forma intermitente. Con el lock, la segunda espera acá, ve la fila de
la primera ya confirmada y recibe el 23P01 limpio. El lock se toma siempre antes que cualquier otra fila (pack,
turno nuevo): un solo orden, sin ciclos. Se libera al cerrar la transacción. Un lock por (tenant, profesional): no
frena a otros profesionales.
"""

import uuid
from typing import Any

SQL_BLOQUEAR_PROFESIONAL = (
    "select pg_advisory_xact_lock(hashtextextended('turnos_profesional:' || $1::text || ':' || $2::text, 0))"
)


async def bloquear_profesional(conn: Any, *, tenant_id: uuid.UUID, profesional_id: uuid.UUID) -> None:
    """Toma el lock dentro de la transacción en curso. Los ids van como texto: es parte de la clave."""
    await conn.execute(SQL_BLOQUEAR_PROFESIONAL, str(tenant_id), str(profesional_id))
