"""Dobles de prueba compartidos por los tests unitarios de platform_admin: conexión y pool falsos, sin base."""

import uuid
from contextlib import asynccontextmanager


class ConexionFalsa:
    """Responde por fragmento de SQL (el primero que coincida); guarda todo lo ejecutado."""

    def __init__(self, respuestas=None, falla_en=None):
        self.respuestas = respuestas or {}
        self.falla_en = falla_en  # (fragmento de SQL, excepción a lanzar)
        self.comandos: list[tuple[str, tuple]] = []

    def _responder(self, sql, args):
        self.comandos.append((sql, args))
        if self.falla_en and self.falla_en[0] in sql:
            raise self.falla_en[1]
        if "write_audit_log" in sql:
            return uuid.uuid4()
        for fragmento, valor in self.respuestas.items():
            if fragmento in sql:
                return valor
        return None

    async def execute(self, sql, *args):
        self._responder(sql, args)
        return "OK"

    async def fetchval(self, sql, *args):
        return self._responder(sql, args)

    async def fetchrow(self, sql, *args):
        return self._responder(sql, args)

    async def fetch(self, sql, *args):
        return self._responder(sql, args) or []

    def sqls(self):
        return [c[0] for c in self.comandos]

    def auditorias(self):
        return [c[1] for c in self.comandos if "write_audit_log" in c[0]]

    def con(self, fragmento):
        return [c for c in self.comandos if fragmento in c[0]]


class PoolFalso:
    """Cada `acquire` devuelve la siguiente conexión de la lista (una por transacción). Sin más, falla."""

    def __init__(self, *conexiones):
        self.conexiones = list(conexiones)
        self.usadas: list[ConexionFalsa] = []

    @asynccontextmanager
    async def acquire(self):
        conn = self.conexiones.pop(0)
        self.usadas.append(conn)
        yield conn
