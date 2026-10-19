"""Conexión que abre una transacción corta por cada consulta (ARCHITECTURE.md §4.3)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

FabricaTx = Callable[[], AbstractAsyncContextManager[Any]]


class ConexionBajoDemanda:
    """Conexión con la interfaz mínima de asyncpg que abre una transacción corta por cada consulta.

    Quien necesita la base solo para lecturas puntuales mientras espera a un tercero (p. ej. Google Calendar) no debe
    retener la conexión de una transacción. Con este objeto cada consulta toma una conexión del pool, la usa y la
    devuelve de inmediato: durante las llamadas HTTP no hay ninguna conexión tomada. `abrir_tx` es una función sin
    argumentos que devuelve el context manager de la transacción (típicamente `tenant_tx` ya con su contexto).
    """

    def __init__(self, abrir_tx: FabricaTx) -> None:
        self._abrir_tx = abrir_tx

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetchval(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.execute(sql, *args)
