"""Soporte de los tests de clientes: conexión falsa que habla el SQL de `clientes` y un calendario simulado.

Datos ficticios: nombres inventados y teléfonos del rango de ejemplo.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from platform_auth import VerificadorJWT
from platform_calendar import EventoNoLimpiado

from dashboard_backend import clientes as c
from dashboard_backend.app import crear_app
from dashboard_backend.tests.soporte import AUD, SECRETO, ConnFalsa, PoolFalso, SupabaseSimulado, config_prueba


class ConnClientes(ConnFalsa):
    def __init__(self):
        super().__init__()
        self.clientes: dict[uuid.UUID, dict] = {}
        self.settings: dict = {}
        self.targets: list[dict] = []
        self.duplicado: uuid.UUID | None = None
        self.auditoria: list[tuple] = []

    def sentencias_que_contienen(self, texto: str) -> list[str]:
        return [s for s in self.sentencias() if texto in s]

    def _fila(self, **kw):
        base = {
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "channel": None,
            "phone": None,
            "name": None,
            "extra_data": "{}",
            "deleted_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),  # noqa: UP017 (el entorno local puede ser 3.10),
        }
        base.update(kw)
        return base

    async def fetchrow(self, sql, *args):
        if sql == c.SQL_DETALLE:
            self.ejecutado.append((sql, args))
            return self.clientes.get(args[1])
        if sql == c.SQL_INSERTAR:
            self.ejecutado.append((sql, args))
            fila = self._fila(tenant_id=args[0], phone=args[1], name=args[2], extra_data=args[3])
            self.clientes[fila["id"]] = fila
            return fila
        if sql == c.SQL_ACTUALIZAR:
            self.ejecutado.append((sql, args))
            fila = self.clientes[args[1]]
            if args[2]:
                fila["phone"] = args[3]
            if args[4]:
                fila["name"] = args[5]
            if args[6]:
                fila["extra_data"] = args[7]
            return fila
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if sql == c.SQL_SETTINGS:
            self.ejecutado.append((sql, args))
            return json.dumps(self.settings)
        if sql == c.SQL_EXISTE:
            self.ejecutado.append((sql, args))
            return 1 if args[1] in self.clientes else None
        if sql == c.SQL_DUPLICADO:
            self.ejecutado.append((sql, args))
            return self.duplicado
        if sql == c.SQL_BLOQUEO_ALTA:
            self.ejecutado.append((sql, args))
            return None
        if "write_audit_log" in sql:
            self.ejecutado.append((sql, args))
            self.auditoria.append(args)
            return uuid.uuid4()
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        self.ejecutado.append((sql, args))
        if sql == c.SQL_TARGETS:
            return self.targets
        return []

    async def execute(self, sql, *args):
        await super().execute(sql, *args)
        if c.SQL_ANONIMIZAR in sql:
            self.clientes[args[0]]["deleted_at"] = datetime.now(timezone.utc)  # noqa: UP017
            self.clientes[args[0]]["phone"] = None
            self.clientes[args[0]]["name"] = None


class CalendarioSimulado:
    """Solo lo que usa el endpoint: `limpiar_eventos`. Registra cuándo se llamó respecto de los COMMIT."""

    def __init__(self, conn: ConnClientes, fallidos=()):
        self.conn = conn
        self.fallidos = list(fallidos)
        self.pares: list | None = None
        self.sentencias_al_limpiar: list[str] | None = None

    async def limpiar_eventos(self, pares):
        self.pares = list(pares)
        self.sentencias_al_limpiar = self.conn.sentencias()[:]
        return self.fallidos


def fabrica_de(servicio):
    @asynccontextmanager
    async def _fabrica(_conn):
        yield servicio

    return _fabrica


def evento_no_limpiado(event_id="evt-1", motivo="token_vencido"):
    return EventoNoLimpiado(uuid.uuid4(), event_id, motivo)


def app_clientes(*, servicio=None, fabrica=None, pool=None):
    """App con el pool falso (o el real, si se pasa `pool`). Devuelve `(app, conn, calendario)`.

    `servicio` es una función `conn -> calendario simulado`; sin él (ni `fabrica`) se usa la fábrica por defecto
    de la app, que lee el entorno.
    """
    conn = None
    if pool is None:
        conn = ConnClientes()
        pool = PoolFalso()
        pool.conn = conn
    calendario = servicio(conn) if servicio is not None else None
    fabrica = fabrica or (fabrica_de(calendario) if calendario is not None else None)
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        configurar_observabilidad=False,
        fabrica_calendario=fabrica,
    )
    return app, conn, calendario
