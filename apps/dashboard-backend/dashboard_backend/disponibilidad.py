"""Dominio de la disponibilidad (reglas semanales y bloqueos, ARCHITECTURE.md §7.1): sin FastAPI.

Las validaciones de forma las hacen los esquemas de entrada; acá viven el SQL, el armado del UPDATE parcial y la
auditoría, que se escribe en la misma transacción que el cambio (si el audit falla, el cambio se revierte). Cada
función recibe la conexión de la transacción abierta por el endpoint y el tenant de la sesión. Que la regla o el
bloqueo no exista (o sea de otro tenant) se informa con `ErrorReglaInexistente` / `ErrorBloqueoInexistente`; los
errores de la base (referencia inválida, `check`) los traduce el router.
"""

import uuid
from datetime import datetime
from typing import Any

from platform_audit import registrar

from dashboard_backend.sql_parcial import armar_set

COLUMNAS_REGLA = "id, tenant_id, professional_id, day_of_week, start_time, end_time, created_at"
COLUMNAS_BLOQUEO = (
    "id, tenant_id, professional_id, lower(time_range) as start_at, upper(time_range) as end_at, reason, created_at"
)

SQL_LISTAR_REGLAS = f"""
select {COLUMNAS_REGLA} from public.availability_rules
where tenant_id = $1 and ($2::uuid is null or professional_id = $2::uuid)
order by professional_id, day_of_week, start_time, id
limit $3 offset $4
"""
SQL_VER_REGLA = f"select {COLUMNAS_REGLA} from public.availability_rules where tenant_id = $1 and id = $2"
SQL_ALTA_REGLA = f"""
insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
values ($1, $2, $3, $4, $5)
returning {COLUMNAS_REGLA}
"""
SQL_BORRAR_REGLA = (
    "delete from public.availability_rules where tenant_id = $1 and id = $2 returning id, professional_id"
)
SQL_LISTAR_BLOQUEOS = f"""
select {COLUMNAS_BLOQUEO} from public.availability_blocks
where tenant_id = $1
  and ($2::uuid is null or professional_id = $2::uuid)
  and ($3::timestamptz is null or upper(time_range) > $3::timestamptz)
  and ($4::timestamptz is null or lower(time_range) < $4::timestamptz)
order by lower(time_range), id
limit $5 offset $6
"""
SQL_VER_BLOQUEO = f"select {COLUMNAS_BLOQUEO} from public.availability_blocks where tenant_id = $1 and id = $2"
SQL_ALTA_BLOQUEO = f"""
insert into public.availability_blocks (tenant_id, professional_id, time_range, reason)
values ($1, $2, tstzrange($3::timestamptz, $4::timestamptz, '[)'), $5)
returning {COLUMNAS_BLOQUEO}
"""
SQL_BORRAR_BLOQUEO = (
    "delete from public.availability_blocks where tenant_id = $1 and id = $2 returning id, professional_id"
)

__all__ = [
    "ErrorBloqueoInexistente",
    "ErrorReglaInexistente",
    "armar_update_bloqueo",
    "armar_update_regla",
    "borrar_bloqueo",
    "borrar_regla",
    "crear_bloqueo",
    "crear_regla",
    "listar_bloqueos",
    "listar_reglas",
    "modificar_bloqueo",
    "modificar_regla",
    "ver_bloqueo",
    "ver_regla",
]


class ErrorReglaInexistente(Exception):
    """La regla no existe (o es de otro tenant)."""


class ErrorBloqueoInexistente(Exception):
    """El bloqueo no existe (o es de otro tenant)."""


def _texto(valor: uuid.UUID | None) -> str | None:
    return None if valor is None else str(valor)


# --- reglas semanales -------------------------------------------------------------------------------------------


def armar_update_regla(cambios: dict[str, Any]) -> tuple[str, list[Any]]:
    asignaciones, valores = armar_set(cambios, primer_parametro=3)
    sql = (
        f"update public.availability_rules set {asignaciones} where tenant_id = $1 and id = $2 "
        f"returning {COLUMNAS_REGLA}"
    )
    return sql, valores


async def listar_reglas(
    conn: Any, tenant_id: uuid.UUID, professional_id: uuid.UUID | None, limite: int, offset: int
) -> list[dict[str, Any]]:
    filas = await conn.fetch(SQL_LISTAR_REGLAS, tenant_id, professional_id, limite, offset)
    return [dict(f) for f in filas]


async def ver_regla(conn: Any, tenant_id: uuid.UUID, regla_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_VER_REGLA, tenant_id, regla_id)
    if fila is None:
        raise ErrorReglaInexistente
    return dict(fila)


async def crear_regla(
    conn: Any, tenant_id: uuid.UUID, professional_id: uuid.UUID, day_of_week: int, start_time: Any, end_time: Any
) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_ALTA_REGLA, tenant_id, professional_id, day_of_week, start_time, end_time)
    await registrar(
        conn,
        "availability_rule_created",
        "availability_rules",
        fila["id"],
        {"professional_id": str(professional_id), "day_of_week": day_of_week},
    )
    return dict(fila)


async def modificar_regla(
    conn: Any, tenant_id: uuid.UUID, regla_id: uuid.UUID, cambios: dict[str, Any]
) -> dict[str, Any]:
    sql, valores = armar_update_regla(cambios)
    fila = await conn.fetchrow(sql, tenant_id, regla_id, *valores)
    if fila is None:
        raise ErrorReglaInexistente
    await registrar(
        conn,
        "availability_rule_updated",
        "availability_rules",
        regla_id,
        {"campos": sorted(cambios), "professional_id": str(fila["professional_id"])},
    )
    return dict(fila)


async def borrar_regla(conn: Any, tenant_id: uuid.UUID, regla_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(SQL_BORRAR_REGLA, tenant_id, regla_id)
    if fila is None:
        raise ErrorReglaInexistente
    await registrar(
        conn,
        "availability_rule_deleted",
        "availability_rules",
        regla_id,
        {"professional_id": str(fila["professional_id"])},
    )


# --- bloqueos ---------------------------------------------------------------------------------------------------


def armar_update_bloqueo(cambios: dict[str, Any]) -> tuple[str, list[Any]]:
    """UPDATE parcial del bloqueo. El extremo del rango que no se envía conserva su valor actual."""
    resto = dict(cambios)
    inicio, fin = resto.pop("start_at", None), resto.pop("end_at", None)
    asignaciones, valores = armar_set(resto, primer_parametro=3)
    partes = [asignaciones] if asignaciones else []
    if inicio is not None or fin is not None:
        n = 3 + len(valores)
        partes.append(
            f"time_range = tstzrange(coalesce(${n}::timestamptz, lower(time_range)), "
            f"coalesce(${n + 1}::timestamptz, upper(time_range)), '[)')"
        )
        valores += [inicio, fin]
    sql = (
        f"update public.availability_blocks set {', '.join(partes)} where tenant_id = $1 and id = $2 "
        f"returning {COLUMNAS_BLOQUEO}"
    )
    return sql, valores


async def listar_bloqueos(
    conn: Any,
    tenant_id: uuid.UUID,
    professional_id: uuid.UUID | None,
    desde: datetime | None,
    hasta: datetime | None,
    limite: int,
    offset: int,
) -> list[dict[str, Any]]:
    filas = await conn.fetch(SQL_LISTAR_BLOQUEOS, tenant_id, professional_id, desde, hasta, limite, offset)
    return [dict(f) for f in filas]


async def ver_bloqueo(conn: Any, tenant_id: uuid.UUID, bloqueo_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_VER_BLOQUEO, tenant_id, bloqueo_id)
    if fila is None:
        raise ErrorBloqueoInexistente
    return dict(fila)


async def crear_bloqueo(
    conn: Any,
    tenant_id: uuid.UUID,
    professional_id: uuid.UUID | None,
    start_at: datetime,
    end_at: datetime,
    reason: str | None,
) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_ALTA_BLOQUEO, tenant_id, professional_id, start_at, end_at, reason)
    await registrar(
        conn,
        "availability_block_created",
        "availability_blocks",
        fila["id"],
        {"professional_id": _texto(professional_id)},
    )
    return dict(fila)


async def modificar_bloqueo(
    conn: Any, tenant_id: uuid.UUID, bloqueo_id: uuid.UUID, cambios: dict[str, Any]
) -> dict[str, Any]:
    sql, valores = armar_update_bloqueo(cambios)
    fila = await conn.fetchrow(sql, tenant_id, bloqueo_id, *valores)
    if fila is None:
        raise ErrorBloqueoInexistente
    await registrar(
        conn,
        "availability_block_updated",
        "availability_blocks",
        bloqueo_id,
        {"campos": sorted(cambios), "professional_id": _texto(fila["professional_id"])},
    )
    return dict(fila)


async def borrar_bloqueo(conn: Any, tenant_id: uuid.UUID, bloqueo_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(SQL_BORRAR_BLOQUEO, tenant_id, bloqueo_id)
    if fila is None:
        raise ErrorBloqueoInexistente
    await registrar(
        conn,
        "availability_block_deleted",
        "availability_blocks",
        bloqueo_id,
        {"professional_id": _texto(fila["professional_id"])},
    )
