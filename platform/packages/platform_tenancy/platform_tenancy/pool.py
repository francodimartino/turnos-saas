"""Creación del pool asyncpg (conexión directa a Postgres)."""

from typing import Any


def opciones_pool(*, statement_cache_size: int | None = None, **extra: Any) -> dict[str, Any]:
    """Arma los kwargs de `asyncpg.create_pool`.

    `statement_cache_size=0` es obligatorio si se conecta por el pooler de Supabase en modo
    transacción, que no soporta prepared statements con nombre (§4.3). Si es None se deja el
    valor por defecto de asyncpg (conexión directa).
    """
    if statement_cache_size is not None and statement_cache_size < 0:
        raise ValueError("statement_cache_size no puede ser negativo")
    opciones: dict[str, Any] = dict(extra)
    if statement_cache_size is not None:
        opciones["statement_cache_size"] = statement_cache_size
    return opciones


async def crear_pool(dsn: str, *, statement_cache_size: int | None = None, **extra: Any):
    """Crea el pool asyncpg. `extra` se pasa tal cual (`min_size`, `max_size`, `command_timeout`...)."""
    import asyncpg

    return await asyncpg.create_pool(dsn, **opciones_pool(statement_cache_size=statement_cache_size, **extra))
