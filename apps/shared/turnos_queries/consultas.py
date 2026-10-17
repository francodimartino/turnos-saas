"""Consultas de lectura compartidas por dashboard-backend y admin-backend (ARCHITECTURE.md §2.11.4, §5.4).

CONDICIÓN DE SEGURIDAD: toda query filtra `tenant_id = $1` de forma explícita en el SQL, sin confiar
solo en la RLS: aunque `platform_admin_readonly` ya no bypassea la RLS (readonly_tx fija el tenant de
la impersonación), el `WHERE tenant_id = $1` es una segunda barrera independiente. Por eso:

- `tenant_id` es siempre un parámetro obligatorio, keyword-only y sin default;
- el `$1` de cada query es el tenant, y también en cada tabla unida con JOIN;
- el SQL vive en constantes `SQL_*` que un test estático inspecciona.

Las funciones reciben la conexión de una transacción ya abierta (`tenant_tx` o `readonly_tx` de
platform_tenancy) y devuelven listas de dicts; no abren transacciones ni escriben.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

LIMITE_POR_DEFECTO = 200

SQL_LISTAR_BOOKINGS = """
select b.id, b.tenant_id, b.end_customer_id, b.service_id, b.professional_id,
       lower(b.time_range) as start_at, upper(b.time_range) as end_at,
       b.status, b.deposit_status, b.booking_pack_id, b.pending_expires_at, b.created_at,
       ec.name as customer_name, s.name as service_name, p.name as professional_name
from public.bookings b
join public.end_customers ec on ec.id = b.end_customer_id and ec.tenant_id = $1
join public.services s on s.id = b.service_id and s.tenant_id = $1
join public.professionals p on p.id = b.professional_id and p.tenant_id = $1
where b.tenant_id = $1
  and b.time_range && tstzrange($2::timestamptz, $3::timestamptz)
  and ($4::text[] is null or b.status = any($4::text[]))
  and ($5::uuid is null or b.professional_id = $5::uuid)
order by lower(b.time_range), b.id
limit $6 offset $7
"""

SQL_DETALLE_BOOKING = """
select b.id, b.tenant_id, b.end_customer_id, b.service_id, b.professional_id,
       lower(b.time_range) as start_at, upper(b.time_range) as end_at,
       b.status, b.deposit_status, b.booking_pack_id, b.pending_expires_at,
       b.google_calendar_event_id, b.created_at,
       ec.name as customer_name, ec.phone as customer_phone,
       s.name as service_name, s.duration_minutes as service_duration_minutes, s.price as service_price,
       p.name as professional_name
from public.bookings b
join public.end_customers ec on ec.id = b.end_customer_id and ec.tenant_id = $1
join public.services s on s.id = b.service_id and s.tenant_id = $1
join public.professionals p on p.id = b.professional_id and p.tenant_id = $1
where b.tenant_id = $1 and b.id = $2
"""

SQL_LISTAR_CLIENTES = """
select ec.id, ec.tenant_id, ec.channel, ec.phone, ec.name, ec.deleted_at, ec.created_at
from public.end_customers ec
where ec.tenant_id = $1
  and ($2::text is null or ec.name ilike '%' || $2::text || '%' or ec.phone ilike '%' || $2::text || '%')
order by ec.created_at, ec.id
limit $3 offset $4
"""

SQL_LISTAR_SERVICIOS = """
select s.id, s.tenant_id, s.name, s.duration_minutes, s.price, s.deposit_amount, s.is_active, s.created_at
from public.services s
where s.tenant_id = $1 and ($2::boolean or s.is_active)
order by s.name, s.id
limit $3 offset $4
"""

SQL_LISTAR_PROFESIONALES = """
select p.id, p.tenant_id, p.name, p.is_active, p.tenant_user_id, p.created_at
from public.professionals p
where p.tenant_id = $1 and ($2::boolean or p.is_active)
order by p.name, p.id
limit $3 offset $4
"""

SQL_LISTAR_PACKS = """
select bp.id, bp.tenant_id, bp.end_customer_id, bp.service_id, bp.total_sessions, bp.used_sessions,
       bp.origin, bp.payment_status, bp.expires_at, bp.created_at
from public.booking_packs bp
where bp.tenant_id = $1 and ($2::uuid is null or bp.end_customer_id = $2::uuid)
order by bp.created_at, bp.id
limit $3 offset $4
"""


def _filas(registros: Sequence[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in registros]


async def listar_bookings(
    conn: Any,
    *,
    tenant_id: UUID,
    desde: datetime,
    hasta: datetime,
    estados: Sequence[str] | None = None,
    professional_id: UUID | None = None,
    limite: int = LIMITE_POR_DEFECTO,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Bookings del tenant cuyo horario se superpone con [desde, hasta), opcionalmente por estado/profesional."""
    registros = await conn.fetch(
        SQL_LISTAR_BOOKINGS,
        tenant_id,
        desde,
        hasta,
        None if estados is None else list(estados),
        professional_id,
        limite,
        offset,
    )
    return _filas(registros)


async def detalle_booking(conn: Any, *, tenant_id: UUID, booking_id: UUID) -> dict[str, Any] | None:
    """Un booking del tenant con los datos del cliente, servicio y profesional; None si no es de ese tenant."""
    registro = await conn.fetchrow(SQL_DETALLE_BOOKING, tenant_id, booking_id)
    return None if registro is None else dict(registro)


async def listar_clientes(
    conn: Any,
    *,
    tenant_id: UUID,
    busqueda: str | None = None,
    limite: int = LIMITE_POR_DEFECTO,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Clientes finales del tenant; `busqueda` filtra por nombre o teléfono (subcadena, sin distinguir mayúsculas)."""
    return _filas(await conn.fetch(SQL_LISTAR_CLIENTES, tenant_id, busqueda, limite, offset))


async def listar_servicios(
    conn: Any,
    *,
    tenant_id: UUID,
    incluir_inactivos: bool = False,
    limite: int = LIMITE_POR_DEFECTO,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Servicios del tenant (por defecto solo los activos)."""
    return _filas(await conn.fetch(SQL_LISTAR_SERVICIOS, tenant_id, incluir_inactivos, limite, offset))


async def listar_profesionales(
    conn: Any,
    *,
    tenant_id: UUID,
    incluir_inactivos: bool = False,
    limite: int = LIMITE_POR_DEFECTO,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Profesionales del tenant (por defecto solo los activos)."""
    return _filas(await conn.fetch(SQL_LISTAR_PROFESIONALES, tenant_id, incluir_inactivos, limite, offset))


async def listar_packs(
    conn: Any,
    *,
    tenant_id: UUID,
    end_customer_id: UUID | None = None,
    limite: int = LIMITE_POR_DEFECTO,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Packs de sesiones del tenant, opcionalmente de un solo cliente."""
    return _filas(await conn.fetch(SQL_LISTAR_PACKS, tenant_id, end_customer_id, limite, offset))
