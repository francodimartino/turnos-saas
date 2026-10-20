"""Apoyo para tests con conexiones falsas: imita `conn.transaction()` de asyncpg sobre `execute`."""

from contextlib import suppress
from typing import Any


class _TransaccionFalsa:
    def __init__(self, conn: Any, solo_lectura: bool) -> None:
        self._conn = conn
        self._solo_lectura = solo_lectura

    async def __aenter__(self) -> "_TransaccionFalsa":
        await self._conn.execute("BEGIN READ ONLY" if self._solo_lectura else "BEGIN")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            await self._conn.execute("COMMIT")
            return
        # Como asyncpg ante un error del cuerpo: ROLLBACK y se propaga la excepción original.
        with suppress(Exception):
            await self._conn.execute("ROLLBACK")


class TransaccionPorExecute:
    """Mixin para conexiones falsas: `transaction()` emite BEGIN/COMMIT/ROLLBACK por `self.execute`."""

    def transaction(self, *, readonly: bool = False) -> _TransaccionFalsa:
        return _TransaccionFalsa(self, readonly)
