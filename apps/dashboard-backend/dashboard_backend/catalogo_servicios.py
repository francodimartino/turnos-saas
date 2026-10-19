"""Dominio de los servicios del local (ARCHITECTURE.md §7.1): sin FastAPI.

Cada función recibe la conexión de la transacción abierta por el endpoint y el tenant de la sesión. La auditoría se
escribe en la misma transacción que el cambio (si falla, el cambio se revierte); su metadata lleva nombres de campos y
el precio anterior y el nuevo, nunca nombres ni datos personales. Que el servicio no exista (o sea de otro tenant) se
informa con `ErrorServicioInexistente`; los errores de la base los traduce el router.
"""

import uuid
from typing import Any

from platform_audit import registrar
from turnos_queries import listar_servicios as _listar_servicios

from dashboard_backend.sql_parcial import armar_set

COLUMNAS_SERVICIO = "id, tenant_id, name, duration_minutes, price, deposit_amount, is_active, created_at"

SQL_VER_SERVICIO = f"select {COLUMNAS_SERVICIO} from public.services where tenant_id = $1 and id = $2"
SQL_ALTA_SERVICIO = f"""
insert into public.services (tenant_id, name, duration_minutes, price, deposit_amount, is_active)
values ($1, $2, $3, $4, $5, $6)
returning {COLUMNAS_SERVICIO}
"""
SQL_PRECIO_PREVIO = "select price from public.services where tenant_id = $1 and id = $2 for update"
SQL_BORRAR_SERVICIO = "delete from public.services where tenant_id = $1 and id = $2 returning id, price"

__all__ = [
    "ErrorServicioInexistente",
    "armar_update_servicio",
    "bloque_precio",
    "borrar_servicio",
    "crear_servicio",
    "listar_servicios",
    "modificar_servicio",
    "ver_servicio",
]


class ErrorServicioInexistente(Exception):
    """El servicio no existe (o es de otro tenant)."""


def _numero(valor: Any) -> float | int | None:
    """Precio para la metadata del audit: JSON numérico (asyncpg devuelve Decimal)."""
    if valor is None:
        return None
    n = float(valor)
    return int(n) if n.is_integer() else n


def bloque_precio(anterior: Any, nuevo: Any) -> dict[str, Any]:
    return {"precio": {"anterior": _numero(anterior), "nuevo": _numero(nuevo)}}


def armar_update_servicio(cambios: dict[str, Any]) -> tuple[str, list[Any]]:
    asignaciones, valores = armar_set(cambios, primer_parametro=3)
    sql = f"update public.services set {asignaciones} where tenant_id = $1 and id = $2 returning {COLUMNAS_SERVICIO}"
    return sql, valores


async def listar_servicios(conn: Any, tenant_id: uuid.UUID, incluir_inactivos: bool, limite: int, offset: int) -> Any:
    return await _listar_servicios(
        conn, tenant_id=tenant_id, incluir_inactivos=incluir_inactivos, limite=limite, offset=offset
    )


async def ver_servicio(conn: Any, tenant_id: uuid.UUID, servicio_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_VER_SERVICIO, tenant_id, servicio_id)
    if fila is None:
        raise ErrorServicioInexistente
    return dict(fila)


async def crear_servicio(
    conn: Any,
    tenant_id: uuid.UUID,
    *,
    name: str,
    duration_minutes: int,
    price: Any,
    deposit_amount: Any,
    is_active: bool,
    campos: set[str],
) -> dict[str, Any]:
    """`campos` son los que el cliente envió; `is_active` se audita siempre porque tiene valor por defecto."""
    fila = await conn.fetchrow(SQL_ALTA_SERVICIO, tenant_id, name, duration_minutes, price, deposit_amount, is_active)
    await registrar(
        conn,
        "service_created",
        "services",
        fila["id"],
        {"campos": sorted(campos | {"is_active"}), **bloque_precio(None, price)},
    )
    return dict(fila)


async def modificar_servicio(
    conn: Any, tenant_id: uuid.UUID, servicio_id: uuid.UUID, cambios: dict[str, Any]
) -> dict[str, Any]:
    sql, valores = armar_update_servicio(cambios)
    previo = None
    if "price" in cambios:
        previo = await conn.fetchrow(SQL_PRECIO_PREVIO, tenant_id, servicio_id)
    fila = await conn.fetchrow(sql, tenant_id, servicio_id, *valores)
    if fila is None:
        raise ErrorServicioInexistente
    meta: dict[str, Any] = {"campos": sorted(cambios)}
    if previo is not None:
        meta.update(bloque_precio(previo["price"], fila["price"]))
    await registrar(conn, "service_updated", "services", servicio_id, meta)
    return dict(fila)


async def borrar_servicio(conn: Any, tenant_id: uuid.UUID, servicio_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(SQL_BORRAR_SERVICIO, tenant_id, servicio_id)
    if fila is None:
        raise ErrorServicioInexistente
    await registrar(conn, "service_deleted", "services", servicio_id, bloque_precio(fila["price"], None))
