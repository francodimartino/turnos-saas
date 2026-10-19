"""Dominio de los servicios que hace cada profesional (`professional_services`, ARCHITECTURE.md §7.1): sin FastAPI.

Cada función recibe la conexión de la transacción abierta por el endpoint y el tenant de la sesión. La auditoría se
escribe en la misma transacción que el cambio (si falla, el cambio se revierte). Un profesional o servicio
inexistente (o de otro tenant) se informa con `ErrorProfesionalInexistente` / `ErrorServicioInexistente` y una
asignación inexistente con `ErrorAsignacionInexistente`; los errores de la base los traduce el router.
"""

import uuid
from typing import Any

from platform_audit import registrar

from dashboard_backend.catalogo_profesionales import ErrorProfesionalInexistente, existe_profesional
from dashboard_backend.catalogo_servicios import ErrorServicioInexistente

COLUMNAS_PS = "professional_id, service_id, confirmation_mode"

SQL_LISTAR_PS = f"""
select {COLUMNAS_PS} from public.professional_services
where tenant_id = $1 and professional_id = $2
order by service_id
"""
SQL_UPSERT_PS = f"""
insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
values ($1, $2, $3, $4)
on conflict (professional_id, service_id) do update set confirmation_mode = excluded.confirmation_mode
returning {COLUMNAS_PS}
"""
SQL_BORRAR_PS = """
delete from public.professional_services
where tenant_id = $1 and professional_id = $2 and service_id = $3
returning service_id, confirmation_mode
"""
SQL_EXISTE_SERVICIO = "select 1 from public.services where tenant_id = $1 and id = $2"
# Serializa las asignaciones del mismo (profesional, servicio): `for update` no cubre una fila que todavía no existe,
# y sin esto dos PUT simultáneos sobre una asignación nueva leerían ambos "sin previo" y auditarían dos altas.
SQL_BLOQUEO_PS = "select pg_advisory_xact_lock(hashtextextended($1::uuid::text || ':' || $2::uuid::text, 0))"
SQL_MODO_PS = """
select confirmation_mode from public.professional_services
where tenant_id = $1 and professional_id = $2 and service_id = $3
for update
"""

__all__ = [
    "ErrorAsignacionInexistente",
    "ErrorProfesionalInexistente",
    "ErrorServicioInexistente",
    "asignar_servicio",
    "quitar_servicio",
    "servicios_del_profesional",
]


class ErrorAsignacionInexistente(Exception):
    """El profesional no tiene asignado ese servicio."""


async def servicios_del_profesional(conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID) -> list[dict[str, Any]]:
    await existe_profesional(conn, tenant_id, profesional_id)
    return [dict(f) for f in await conn.fetch(SQL_LISTAR_PS, tenant_id, profesional_id)]


async def asignar_servicio(
    conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID, servicio_id: uuid.UUID, confirmation_mode: str
) -> dict[str, Any]:
    await existe_profesional(conn, tenant_id, profesional_id)
    if await conn.fetchval(SQL_EXISTE_SERVICIO, tenant_id, servicio_id) is None:
        raise ErrorServicioInexistente
    await conn.execute(SQL_BLOQUEO_PS, profesional_id, servicio_id)
    previo = await conn.fetchval(SQL_MODO_PS, tenant_id, profesional_id, servicio_id)
    fila = await conn.fetchrow(SQL_UPSERT_PS, tenant_id, profesional_id, servicio_id, confirmation_mode)
    await registrar(
        conn,
        "professional_service_assigned" if previo is None else "professional_service_updated",
        "professional_services",
        profesional_id,
        {"service_id": str(servicio_id), "confirmation_mode": {"anterior": previo, "nuevo": confirmation_mode}},
    )
    return dict(fila)


async def quitar_servicio(conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID, servicio_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(SQL_BORRAR_PS, tenant_id, profesional_id, servicio_id)
    if fila is None:
        raise ErrorAsignacionInexistente
    await registrar(
        conn,
        "professional_service_removed",
        "professional_services",
        profesional_id,
        {"service_id": str(servicio_id), "confirmation_mode": {"anterior": fila["confirmation_mode"], "nuevo": None}},
    )
