"""Cadena de hashes de audit_log con conexiones reales (tamper-evidence).

pgTAP corre en una sola sesion: acá hay varias. Dos transacciones escriben en el mismo tenant a la vez; la
segunda tiene que esperar el lock de la cadena y, al confirmarse la primera, quedar como siguiente eslabon (sin
ramas ni hashes duplicados). Tambien se prueba que las funciones de aplicacion (write_audit_log) encadenan y que
solo platform_admin ejecuta verify_audit_chain.
"""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def tenant(db_superuser):
    tenant = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Cadena', $2)",
        tenant,
        f"it-cadena-{tenant.hex[:8]}",
    )
    try:
        yield tenant
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenants where id = $1", tenant)


async def _cadena(conn, tenant):
    return await conn.fetch(
        "select chain_seq, prev_hash, hash from public.audit_log where tenant_id = $1 order by chain_seq", tenant
    )


def _es_lineal(filas) -> bool:
    if [f["chain_seq"] for f in filas] != list(range(1, len(filas) + 1)):
        return False
    if filas[0]["prev_hash"] is not None:
        return False
    if len({f["hash"] for f in filas}) != len(filas):
        return False
    return all(filas[i]["prev_hash"] == filas[i - 1]["hash"] for i in range(1, len(filas)))


async def test_dos_inserciones_concurrentes_del_mismo_tenant_quedan_en_cadena_lineal(
    database_url, db_superuser, tenant
):
    a = await asyncpg.connect(database_url)
    b = await asyncpg.connect(database_url)
    try:
        tx = a.transaction()
        await tx.start()
        await a.execute(
            "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', 'it_a')", tenant
        )
        segunda = asyncio.ensure_future(
            b.execute(
                "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', 'it_b')", tenant
            )
        )
        await asyncio.sleep(1)
        assert not segunda.done(), "la segunda inserción del mismo tenant tenía que esperar el lock de la cadena"
        await tx.commit()
        await asyncio.wait_for(segunda, timeout=10)
    finally:
        await a.close()
        await b.close()

    filas = await _cadena(db_superuser, tenant)
    assert len(filas) == 2 and _es_lineal(filas)
    assert await db_superuser.fetchval("select public.verify_audit_chain($1)", tenant) is None


async def test_otro_tenant_no_espera_el_lock(database_url, db_superuser, tenant):
    otro = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Otro', $2)", otro, f"it-otro-{otro.hex[:8]}"
    )
    a = await asyncpg.connect(database_url)
    b = await asyncpg.connect(database_url)
    try:
        tx = a.transaction()
        await tx.start()
        await a.execute(
            "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', 'it_a')", tenant
        )
        await asyncio.wait_for(
            b.execute(
                "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', 'it_b')", otro
            ),
            timeout=5,
        )
        await tx.commit()
    finally:
        await a.close()
        await b.close()
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", otro)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenants where id = $1", otro)


async def test_muchas_escrituras_paralelas_no_rompen_la_cadena(database_url, db_superuser, tenant):
    conexiones = [await asyncpg.connect(database_url) for _ in range(6)]
    try:
        await asyncio.gather(
            *(
                c.execute(
                    "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', $2)",
                    tenant,
                    f"it_{i}_{j}",
                )
                for i, c in enumerate(conexiones)
                for j in range(5)
            )
        )
    finally:
        for c in conexiones:
            await c.close()
    filas = await _cadena(db_superuser, tenant)
    assert len(filas) == 30 and _es_lineal(filas)
    assert await db_superuser.fetchval("select public.verify_audit_chain($1)", tenant) is None


async def test_write_audit_log_encadena_y_solo_platform_admin_verifica(conectar_como, db_superuser, tenant):
    bot = await conectar_como("bot_service")
    async with bot.transaction():
        await bot.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
        await bot.execute("select public.write_audit_log('it_chain')")
    filas = await _cadena(db_superuser, tenant)
    assert len(filas) == 1 and _es_lineal(filas)

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await bot.fetchval("select public.verify_audit_chain($1)", tenant)
    admin = await conectar_como("platform_admin")
    assert await admin.fetchval("select public.verify_audit_chain($1)", tenant) is None
