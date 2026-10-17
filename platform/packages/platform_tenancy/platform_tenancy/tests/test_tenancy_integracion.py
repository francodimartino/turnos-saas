"""Integración de platform_tenancy contra Postgres real: el contexto de tenant aísla de verdad."""

import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

from platform_tenancy import admin_tx, crear_pool, readonly_tx, tenant_tx

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


def _dsn(base: str, rol: str, password: str) -> str:
    p = urlsplit(base)
    host = p.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{host}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, ""))


@pytest.fixture
async def pool_como(database_url, roles_password):
    """Fábrica de pools de 1 conexión (fuerza la reutilización) para un rol de login."""
    pools = []

    async def _crear(rol: str):
        pool = await crear_pool(_dsn(database_url, rol, roles_password), min_size=1, max_size=1, statement_cache_size=0)
        pools.append(pool)
        return pool

    try:
        yield _crear
    finally:
        for pool in pools:
            await pool.close()


@pytest.fixture
async def dos_tenants(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        a, f"it-ten-a-{sufijo}", b, f"it-ten-b-{sufijo}",
    )
    await db_superuser.execute(
        "insert into public.end_customers (tenant_id, channel, channel_user_id) "
        "values ($1, 'telegram', 'ua'), ($2, 'telegram', 'ub')",
        a, b,
    )
    try:
        yield a, b
    finally:
        await db_superuser.execute("delete from public.end_customers where tenant_id = any($1::uuid[])", [a, b])
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", [a, b])


async def test_dentro_de_tenant_tx_solo_se_ven_filas_del_tenant(pool_como, dos_tenants):
    a, b = dos_tenants
    pool = await pool_como("dashboard_service")
    async with tenant_tx(pool, a, uuid.uuid4()) as conn:
        ids = {r["tenant_id"] for r in await conn.fetch("select tenant_id from public.end_customers")}
        propios = await conn.fetchval("select count(*) from public.end_customers where tenant_id = $1", a)
        ajenos = await conn.fetchval("select count(*) from public.end_customers where tenant_id = $1", b)
    assert ids == {a}
    assert (propios, ajenos) == (1, 0)


async def test_el_contexto_no_sobrevive_a_la_transaccion(pool_como, dos_tenants):
    a, _ = dos_tenants
    pool = await pool_como("dashboard_service")
    async with tenant_tx(pool, a):
        pass
    # max_size=1: esta es la misma conexión física, reutilizada.
    async with pool.acquire() as conn:
        guc = await conn.fetchval("select nullif(current_setting('app.current_tenant_id', true), '')")
        filas = await conn.fetchval("select count(*) from public.end_customers")
    assert guc is None
    assert filas == 0


async def test_el_contexto_no_sobrevive_ni_con_error(pool_como, dos_tenants):
    a, _ = dos_tenants
    pool = await pool_como("dashboard_service")
    with pytest.raises(RuntimeError):
        async with tenant_tx(pool, a):
            raise RuntimeError("falla")
    async with pool.acquire() as conn:
        assert await conn.fetchval("select count(*) from public.end_customers") == 0


async def test_query_fuera_de_tenant_tx_devuelve_cero_filas(pool_como, dos_tenants):
    pool = await pool_como("dashboard_service")
    async with pool.acquire() as conn:
        assert await conn.fetchval("select count(*) from public.end_customers") == 0


async def test_actor_queda_seteado_solo_dentro_de_la_transaccion(pool_como, dos_tenants):
    a, _ = dos_tenants
    actor = uuid.uuid4()
    pool = await pool_como("dashboard_service")
    async with tenant_tx(pool, a, actor) as conn:
        assert await conn.fetchval("select current_setting('app.current_actor_id')") == str(actor)
    async with pool.acquire() as conn:
        assert await conn.fetchval("select nullif(current_setting('app.current_actor_id', true), '')") is None


async def test_tenant_tx_con_rollback_no_persiste_escrituras(pool_como, dos_tenants):
    a, _ = dos_tenants
    pool = await pool_como("dashboard_service")
    with pytest.raises(RuntimeError):
        async with tenant_tx(pool, a) as conn:
            await conn.execute(
                "insert into public.end_customers (tenant_id, channel, channel_user_id) values ($1, 'telegram', 'zz')",
                a,
            )
            raise RuntimeError("deshacer")
    async with tenant_tx(pool, a) as conn:
        assert await conn.fetchval("select count(*) from public.end_customers where channel_user_id = 'zz'") == 0


async def test_admin_tx_sin_tenant_opera_como_platform_admin(pool_como, dos_tenants):
    a, b = dos_tenants
    pool = await pool_como("platform_admin")
    async with admin_tx(pool) as conn:
        assert await conn.fetchval("select current_user") == "platform_admin"
        visibles = {r["id"] for r in await conn.fetch("select id from public.tenants")}
    assert {a, b} <= visibles


async def test_readonly_tx_ve_todos_los_tenants_pero_falla_ante_insert(pool_como, dos_tenants):
    a, b = dos_tenants
    pool = await pool_como("platform_admin_readonly")
    async with readonly_tx(pool, a) as conn:
        visibles = {r["id"] for r in await conn.fetch("select id from public.tenants")}
    assert {a, b} <= visibles
    with pytest.raises(asyncpg.PostgresError):
        async with readonly_tx(pool, a) as conn:
            await conn.execute("insert into public.platform_settings (key, value) values ('x', '1'::jsonb)")
    # la conexión sigue sana tras el error
    async with readonly_tx(pool, a) as conn:
        assert await conn.fetchval("select 1") == 1


async def test_readonly_tx_fija_el_tenant_en_la_transaccion(pool_como, dos_tenants):
    a, b = dos_tenants
    pool = await pool_como("platform_admin_readonly")
    async with readonly_tx(pool, a) as conn:
        assert await conn.fetchval("select current_setting('app.current_tenant_id')") == str(a)
    async with readonly_tx(pool, b) as conn:
        assert await conn.fetchval("select current_setting('app.current_tenant_id')") == str(b)


async def test_acquire_con_pool_agotado_lanza_timeout(database_url, roles_password):
    import asyncio

    pool = await crear_pool(
        _dsn(database_url, "dashboard_service", roles_password),
        min_size=1, max_size=1, statement_cache_size=0, acquire_timeout=0.3,
    )
    try:
        async with pool.acquire():
            with pytest.raises(asyncio.TimeoutError):
                async with pool.acquire():
                    pass
    finally:
        await pool.close()
