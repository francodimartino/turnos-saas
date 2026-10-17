"""Carrera purga/reactivación con dos conexiones reales (§2.2, estado intermedio 'purging').

pgTAP corre en una sola sesión y solo puede probar el resultado de la carrera. Acá hay dos: `purge_job` sostiene
`begin_tenant_purge` sin confirmar y, mientras tanto, otra conexión intenta reactivar el tenant. La reactivación
tiene que quedar esperando el lock de la fila y, apenas el job confirma, fallar porque el tenant ya es 'purging'.
"""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def tenant_vencido(db_superuser):
    tenant = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) "
        "values ($1, 'Carrera', $2, 'inactive', now() - interval '100 days')",
        tenant,
        f"it-carrera-{tenant.hex[:8]}",
    )
    try:
        yield tenant
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenants where id = $1", tenant)


async def test_la_reactivacion_concurrente_espera_el_lock_y_falla_al_confirmarse_la_purga(
    conectar_como, database_url, tenant_vencido
):
    job = await conectar_como("purge_job")
    admin = await asyncpg.connect(database_url)
    try:
        tx = job.transaction()
        await tx.start()
        await job.execute("select public.begin_tenant_purge($1)", tenant_vencido)

        reactivar = asyncio.ensure_future(
            admin.execute("update public.tenants set status = 'active' where id = $1", tenant_vencido)
        )
        await asyncio.sleep(1)
        assert not reactivar.done(), "la reactivación tenía que esperar el lock de la purga en curso"

        await tx.commit()
        with pytest.raises(asyncpg.CheckViolationError):
            await asyncio.wait_for(reactivar, timeout=10)
    finally:
        await admin.close()

    en_lista = await job.fetchval(
        "select count(*) from public.list_tenants_due_for_purge() where tenant_id = $1", tenant_vencido
    )
    assert en_lista == 1  # sigue en la lista del job: la purga se retoma
    visto = await asyncpg.connect(database_url)
    try:
        assert await visto.fetchval("select status from public.tenants where id = $1", tenant_vencido) == "purging"
    finally:
        await visto.close()


async def test_si_la_reactivacion_gana_la_purga_se_rechaza(conectar_como, db_superuser, tenant_vencido):
    """El orden inverso: reactivado primero (dentro de la gracia efectiva), begin_tenant_purge ya no aplica."""
    await db_superuser.execute("update public.tenants set status = 'active' where id = $1", tenant_vencido)
    job = await conectar_como("purge_job")
    with pytest.raises(asyncpg.CheckViolationError, match="purga rechazada"):
        await job.execute("select public.begin_tenant_purge($1)", tenant_vencido)
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", tenant_vencido) == "active"
