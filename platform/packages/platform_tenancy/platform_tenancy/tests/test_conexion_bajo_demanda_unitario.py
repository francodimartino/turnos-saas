"""ConexionBajoDemanda: una transacción corta por consulta, sin retener conexión entre una y otra."""

import ast
from contextlib import asynccontextmanager
from pathlib import Path

from platform_tenancy import ConexionBajoDemanda

RAIZ = next(p for p in Path(__file__).resolve().parents if (p / "apps").is_dir() and (p / "platform").is_dir())


class _Conn:
    def __init__(self, log):
        self.log = log

    async def fetchval(self, sql, *args):
        self.log.append(("fetchval", sql, args))
        return 7

    async def fetchrow(self, sql, *args):
        self.log.append(("fetchrow", sql, args))
        return {"a": 1}

    async def fetch(self, sql, *args):
        self.log.append(("fetch", sql, args))
        return [1, 2]

    async def execute(self, sql, *args):
        self.log.append(("execute", sql, args))
        return "OK"


def _fabrica(log, eventos):
    @asynccontextmanager
    async def abrir():
        eventos.append("abre")
        try:
            yield _Conn(log)
        finally:
            eventos.append("cierra")

    return abrir


async def test_cada_consulta_abre_y_cierra_su_propia_transaccion():
    log, eventos = [], []
    c = ConexionBajoDemanda(_fabrica(log, eventos))

    assert await c.fetchval("a", 1) == 7
    assert await c.fetchrow("b") == {"a": 1}
    assert await c.fetch("c") == [1, 2]
    assert await c.execute("d", 2, 3) == "OK"

    assert eventos == ["abre", "cierra"] * 4
    assert [x[0] for x in log] == ["fetchval", "fetchrow", "fetch", "execute"]
    assert log[3][2] == (2, 3)


def test_una_sola_definicion_en_el_repo():
    clases = []
    for base in ("apps", "platform"):
        for ruta in (RAIZ / base).rglob("*.py"):
            if "node_modules" in ruta.parts:
                continue
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            clases += [
                str(ruta) for n in ast.walk(arbol) if isinstance(n, ast.ClassDef) and n.name == "ConexionBajoDemanda"
            ]
    assert len(clases) == 1, clases
