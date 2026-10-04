"""Tests unitarios de platform_tenancy con una conexión falsa: verifican el orden de comandos."""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_tenancy import (
    ContextoTenantInvalido,
    admin_tx,
    opciones_pool,
    readonly_tx,
    tenant_tx,
)

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")


class ConexionFalsa:
    def __init__(self, falla_en: str | None = None):
        self.comandos: list[tuple[str, tuple]] = []
        self.falla_en = falla_en

    async def execute(self, sql: str, *args):
        self.comandos.append((sql, args))
        if self.falla_en and self.falla_en in sql:
            raise RuntimeError(f"falla simulada en {self.falla_en}")
        return "OK"

    async def fetch(self, sql: str, *args):
        self.comandos.append((sql, args))
        return []


class PoolFalso:
    def __init__(self, conexion: ConexionFalsa):
        self.conexion = conexion
        self.adquiridas = 0
        self.liberadas = 0

    @asynccontextmanager
    async def acquire(self):
        self.adquiridas += 1
        try:
            yield self.conexion
        finally:
            self.liberadas += 1


def _sqls(conn: ConexionFalsa) -> list[str]:
    return [c[0] for c in conn.comandos]


async def test_orden_begin_set_local_query_commit():
    conn = ConexionFalsa()
    pool = PoolFalso(conn)
    async with tenant_tx(pool, TENANT, ACTOR) as c:
        await c.fetch("select 1")
    sqls = _sqls(conn)
    assert sqls[0] == "BEGIN"
    assert "set_config('app.current_tenant_id'" in sqls[1]
    assert "set_config('app.current_actor_id'" in sqls[1]
    assert sqls[2] == "select 1"
    assert sqls[3] == "COMMIT"
    assert len(sqls) == 4
    assert pool.adquiridas == pool.liberadas == 1


async def test_contexto_va_parametrizado_no_interpolado():
    conn = ConexionFalsa()
    async with tenant_tx(PoolFalso(conn), TENANT, ACTOR):
        pass
    sql, args = conn.comandos[1]
    assert str(TENANT) not in sql and str(ACTOR) not in sql
    assert args == (str(TENANT), str(ACTOR))


async def test_acepta_tenant_como_string_y_actor_opcional():
    conn = ConexionFalsa()
    async with tenant_tx(PoolFalso(conn), str(TENANT)):
        pass
    assert conn.comandos[1][1] == (str(TENANT), "")


async def test_rollback_ante_excepcion_y_se_propaga():
    conn = ConexionFalsa()
    with pytest.raises(ValueError, match="boom"):
        async with tenant_tx(PoolFalso(conn), TENANT, ACTOR):
            raise ValueError("boom")
    sqls = _sqls(conn)
    assert sqls[-1] == "ROLLBACK"
    assert "COMMIT" not in sqls


async def test_rollback_si_falla_el_set_local():
    conn = ConexionFalsa(falla_en="set_config")
    with pytest.raises(RuntimeError):
        async with tenant_tx(PoolFalso(conn), TENANT, ACTOR):
            pytest.fail("no debe ejecutarse el cuerpo")
    assert _sqls(conn)[-1] == "ROLLBACK"


async def test_si_falla_el_rollback_se_propaga_el_error_original():
    conn = ConexionFalsa(falla_en="ROLLBACK")
    with pytest.raises(ValueError, match="original"):
        async with tenant_tx(PoolFalso(conn), TENANT):
            raise ValueError("original")


@pytest.mark.parametrize("invalido", [None, "", "no-es-uuid", 123, "1111"])
async def test_tenant_invalido_falla_sin_tocar_la_base(invalido):
    conn = ConexionFalsa()
    pool = PoolFalso(conn)
    with pytest.raises(ContextoTenantInvalido):
        async with tenant_tx(pool, invalido, ACTOR):
            pytest.fail("no debe ejecutarse el cuerpo")
    assert conn.comandos == []
    assert pool.adquiridas == 0


async def test_actor_invalido_falla():
    conn = ConexionFalsa()
    with pytest.raises(ContextoTenantInvalido):
        async with tenant_tx(PoolFalso(conn), TENANT, "basura"):
            pass
    assert conn.comandos == []


async def test_admin_tx_no_setea_tenant():
    conn = ConexionFalsa()
    async with admin_tx(PoolFalso(conn)) as c:
        await c.fetch("select 1")
    assert _sqls(conn) == ["BEGIN", "select 1", "COMMIT"]


async def test_admin_tx_con_actor_lo_setea_y_hace_rollback_ante_error():
    conn = ConexionFalsa()
    with pytest.raises(ValueError):
        async with admin_tx(PoolFalso(conn), actor_id=ACTOR):
            raise ValueError("x")
    assert "app.current_actor_id" in conn.comandos[1][0]
    assert "app.current_tenant_id" not in conn.comandos[1][0]
    assert _sqls(conn)[-1] == "ROLLBACK"


async def test_readonly_tx_abre_transaccion_de_solo_lectura():
    conn = ConexionFalsa()
    async with readonly_tx(PoolFalso(conn)) as c:
        await c.fetch("select 1")
    assert _sqls(conn) == ["BEGIN READ ONLY", "select 1", "COMMIT"]


def test_opciones_pool_por_defecto_conserva_cache_de_asyncpg():
    assert "statement_cache_size" not in opciones_pool()


def test_opciones_pool_sin_cache_para_pooler_en_modo_transaccion():
    assert opciones_pool(statement_cache_size=0)["statement_cache_size"] == 0


def test_opciones_pool_rechaza_cache_negativo():
    with pytest.raises(ValueError):
        opciones_pool(statement_cache_size=-1)
