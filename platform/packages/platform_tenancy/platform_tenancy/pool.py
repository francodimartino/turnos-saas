"""Creación del pool asyncpg (conexión directa a Postgres)."""

from typing import Any

# Timeouts finitos por defecto: sin ellos una query colgada o un pool agotado esperan para siempre.
COMMAND_TIMEOUT_DEFECTO = 30.0  # segundos por comando SQL
CONNECT_TIMEOUT_DEFECTO = 10.0  # segundos para abrir una conexión nueva
ACQUIRE_TIMEOUT_DEFECTO = 10.0  # segundos esperando una conexión libre del pool

# Tamaños (min_size, max_size) por servicio. Es la única fuente de tamaños: los servicios la
# consumen vía `crear_pool(..., servicio="<nombre>")`, sin literales propios.
TAMANOS_POOL: dict[str, tuple[int, int]] = {
    "bot": (1, 5),
    "dashboard": (1, 5),
    "admin": (1, 5),
    "admin_readonly": (1, 3),
    "jobs": (1, 3),
}


def opciones_pool(
    *,
    statement_cache_size: int | None = None,
    command_timeout: float | None = COMMAND_TIMEOUT_DEFECTO,
    timeout: float | None = CONNECT_TIMEOUT_DEFECTO,
    acquire_timeout: float | None = ACQUIRE_TIMEOUT_DEFECTO,
    **extra: Any,
) -> dict[str, Any]:
    """Arma los kwargs de `asyncpg.create_pool` (más `acquire_timeout`, que consume `crear_pool`).

    `statement_cache_size=0` es obligatorio si se conecta por el pooler de Supabase en modo
    transacción, que no soporta prepared statements con nombre (§4.3). Si es None se deja el
    valor por defecto de asyncpg (conexión directa).
    """
    if statement_cache_size is not None and statement_cache_size < 0:
        raise ValueError("statement_cache_size no puede ser negativo")
    opciones: dict[str, Any] = dict(extra)
    opciones["command_timeout"] = command_timeout
    opciones["timeout"] = timeout
    opciones["acquire_timeout"] = acquire_timeout
    if statement_cache_size is not None:
        opciones["statement_cache_size"] = statement_cache_size
    return opciones


class PoolConTimeout:
    """Envuelve un pool asyncpg: `acquire()` sin timeout usa `acquire_timeout` en vez de esperar siempre.

    El resto de la API del pool se delega tal cual. Ojo: NO es una instancia de `asyncpg.Pool`
    (es un wrapper por composición); no hay que usar `isinstance(..., asyncpg.Pool)` ni anotar
    con ese tipo un valor devuelto por `crear_pool`. Para llegar al pool real, usar `_pool`.
    """

    def __init__(self, pool: Any, *, acquire_timeout: float | None):
        self._pool = pool
        self._acquire_timeout = acquire_timeout

    def acquire(self, *, timeout: float | None = None):
        return self._pool.acquire(timeout=self._acquire_timeout if timeout is None else timeout)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._pool, nombre)


async def crear_pool(dsn: str, *, statement_cache_size: int | None = None, servicio: str | None = None, **extra: Any):
    """Crea el pool asyncpg con timeouts finitos. `extra` se pasa tal cual (`min_size`, `max_size`...)
    y puede sobrescribir `command_timeout`, `timeout` y `acquire_timeout`.

    Si se indica `servicio`, `min_size`/`max_size` salen de `TAMANOS_POOL` (salvo que se pasen
    explícitos en `extra`); un servicio desconocido lanza `ValueError`.

    Devuelve un `PoolConTimeout`, que NO es un `asyncpg.Pool` (no usar `isinstance` contra él).
    """
    import asyncpg

    if servicio is not None:
        if servicio not in TAMANOS_POOL:
            raise ValueError(f"servicio de pool desconocido: {servicio!r}")
        min_size, max_size = TAMANOS_POOL[servicio]
        extra.setdefault("min_size", min_size)
        extra.setdefault("max_size", max_size)
    opciones = opciones_pool(statement_cache_size=statement_cache_size, **extra)
    acquire_timeout = opciones.pop("acquire_timeout")
    pool = await asyncpg.create_pool(dsn, **opciones)
    return PoolConTimeout(pool, acquire_timeout=acquire_timeout)
