"""Outbox de notificaciones con conexiones reales (`bot_service` y `scheduler_job`).

pgTAP (59_notification_outbox.sql) cubre estructura, aislamiento y grants en una sola sesión. Acá hay varias
conexiones: el encolado atómico con el cambio del llamador (rollback => no queda aviso) y el reclamo concurrente
con FOR UPDATE SKIP LOCKED (dos jobs no reciben la misma fila ni se bloquean entre sí).
"""

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def escenario(db_superuser):
    tenant, cliente = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Outbox', $2)", tenant, f"it-outbox-{tenant.hex[:8]}"
    )
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
        "values ($1, $2, 'telegram', 'u-outbox')",
        cliente,
        tenant,
    )
    try:
        yield tenant, cliente
    finally:
        await db_superuser.execute("delete from public.notification_outbox where tenant_id = $1", tenant)
        await db_superuser.execute("delete from public.end_customers where tenant_id = $1", tenant)
        await db_superuser.execute("delete from public.tenants where id = $1", tenant)


async def _encolar(conn, tenant, cliente, demora=0):
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
    return await conn.fetchval(
        "select public.enqueue_notification($1, 'telegram', 'turno_confirmado', '{\"n\": 1}'::jsonb, $2)",
        cliente,
        demora,
    )


async def test_el_aviso_solo_existe_si_el_cambio_se_confirma(conectar_como, db_superuser, escenario):
    tenant, cliente = escenario
    bot = await conectar_como("bot_service")

    tx = bot.transaction()
    await tx.start()
    await _encolar(bot, tenant, cliente)
    await tx.rollback()
    assert (
        await db_superuser.fetchval("select count(*) from public.notification_outbox where tenant_id = $1", tenant) == 0
    )

    tx = bot.transaction()
    await tx.start()
    id_ = await _encolar(bot, tenant, cliente)
    await tx.commit()
    fila = await db_superuser.fetchrow("select status, attempts from public.notification_outbox where id = $1", id_)
    assert (fila["status"], fila["attempts"]) == ("pending", 0)


async def test_dos_jobs_concurrentes_no_reclaman_la_misma_fila(conectar_como, db_superuser, escenario):
    tenant, cliente = escenario
    bot = await conectar_como("bot_service")
    tx = bot.transaction()
    await tx.start()
    ids = {await _encolar(bot, tenant, cliente) for _ in range(4)}
    await tx.commit()
    assert len(ids) == 4

    job_a = await conectar_como("scheduler_job")
    job_b = await conectar_como("scheduler_job")
    tx_a, tx_b = job_a.transaction(), job_b.transaction()
    await tx_a.start()
    await tx_b.start()
    # A reclama y mantiene su transacción abierta (locks tomados): B no debe bloquearse ni repetir filas
    reclamadas_a = await job_a.fetch("select id, tenant_id from public.claim_notification_outbox(2)")
    reclamadas_b = await asyncio.wait_for(
        job_b.fetch("select id, tenant_id from public.claim_notification_outbox(10)"), 10
    )
    await tx_a.commit()
    await tx_b.commit()

    propias = lambda filas: {f["id"] for f in filas if f["tenant_id"] == tenant}  # noqa: E731
    a, b = propias(reclamadas_a), propias(reclamadas_b)
    assert a.isdisjoint(b)
    assert a | b <= ids

    # con el arriendo vigente, un nuevo reclamo no devuelve lo ya reclamado
    de_nuevo = propias(await job_a.fetch("select id, tenant_id from public.claim_notification_outbox(500)"))
    assert de_nuevo.isdisjoint(a | b)
    assert (a | b | de_nuevo) == ids
