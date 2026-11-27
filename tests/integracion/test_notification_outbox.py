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


async def _en_tenant(conn, tenant, sql, *args):
    """Una sentencia dentro de su propia transacción, con el contexto de tenant (como lo hace el enviador)."""
    async with conn.transaction():
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
        return await conn.fetchval(sql, *args)


async def _encolar_negocio(conn, tenant, demora=0):
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
    return await conn.fetchval(
        "select public.enqueue_notification_negocio('aviso_negocio_reserva', '{}'::jsonb, $1)", demora
    )


async def test_dos_reclamos_concurrentes_no_reciben_la_misma_fila_del_negocio(conectar_como, db_superuser, escenario):
    tenant, _ = escenario
    bot = await conectar_como("bot_service")
    tx = bot.transaction()
    await tx.start()
    ids = {await _encolar_negocio(bot, tenant) for _ in range(6)}
    await tx.commit()
    assert len(ids) == 6

    job_a = await conectar_como("scheduler_job")
    job_b = await conectar_como("scheduler_job")
    tx_a, tx_b = job_a.transaction(), job_b.transaction()
    await tx_a.start()
    await tx_b.start()
    reclamadas_a = await job_a.fetch(
        "select id, tenant_id, end_customer_id, channel from public.claim_notification_outbox(3)"
    )
    reclamadas_b = await asyncio.wait_for(
        job_b.fetch("select id, tenant_id, end_customer_id, channel from public.claim_notification_outbox(10)"), 10
    )
    await tx_a.commit()
    await tx_b.commit()

    propias = lambda filas: {f["id"] for f in filas if f["tenant_id"] == tenant}  # noqa: E731
    a, b = propias(reclamadas_a), propias(reclamadas_b)
    assert a.isdisjoint(b)
    assert a | b <= ids
    # con el arriendo vigente, un nuevo reclamo no repite lo ya reclamado y entrega el resto
    de_nuevo = propias(await job_a.fetch("select id, tenant_id from public.claim_notification_outbox(500)"))
    assert de_nuevo.isdisjoint(a | b)
    assert (a | b | de_nuevo) == ids
    # las filas del negocio no tienen ficha ni canal de origen
    assert all(
        f["end_customer_id"] is None and f["channel"] is None for f in [*reclamadas_a, *reclamadas_b] if f["id"] in ids
    )


async def test_un_canal_que_falla_no_repite_el_que_ya_salio(conectar_como, db_superuser, escenario):
    tenant, cliente = escenario
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'email', 'it-outbox@mail.test', now()) on conflict do nothing",
        tenant,
        cliente,
    )
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'telegram', 'u-outbox', now()) on conflict do nothing",
        tenant,
        cliente,
    )
    claves = {
        f["canal"]: f"cc:{f['id']}"
        for f in await db_superuser.fetch("select id, canal from public.canales_cliente where tenant_id = $1", tenant)
    }
    bot = await conectar_como("bot_service")
    tx = bot.transaction()
    await tx.start()
    id_ = await _encolar(bot, tenant, cliente)
    await tx.commit()

    enviador = await conectar_como("scheduler_job")
    registrar = "select public.registrar_entrega_outbox($1, $2, $3, $4)"
    assert await _en_tenant(enviador, tenant, registrar, id_, claves["telegram"], "sent", None) is True
    assert await _en_tenant(enviador, tenant, registrar, id_, claves["email"], "failed", "timeout") is True
    await _en_tenant(enviador, tenant, "select public.complete_notification_outbox($1, 'pending', 'email', 0)", id_)

    # el reintento lo reclama de nuevo y sabe que el telegram ya salió y que el email no
    reclamadas = await enviador.fetch("select id from public.claim_notification_outbox(50)")
    assert id_ in {f["id"] for f in reclamadas}
    entregas = await db_superuser.fetchval("select entregas::text from public.notification_outbox where id = $1", id_)
    assert "u-outbox" not in entregas and "it-outbox@mail.test" not in entregas
    assert await _en_tenant(enviador, tenant, registrar, id_, claves["telegram"], "failed", "otra vez") is False
    assert await _en_tenant(enviador, tenant, registrar, id_, claves["email"], "sent", None) is True
    assert await _en_tenant(enviador, tenant, "select public.complete_notification_outbox($1, 'sent')", id_) is True
    estados = await db_superuser.fetchval(
        "select jsonb_object_agg(k, v ->> 'estado')::text from public.notification_outbox o, "
        "jsonb_each(o.entregas) as e(k, v) where o.id = $1",
        id_,
    )
    assert estados.count('"sent"') == 2 and '"failed"' not in estados


async def test_dos_registros_concurrentes_del_mismo_canal_no_pisan_lo_entregado(conectar_como, db_superuser, escenario):
    tenant, cliente = escenario
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'telegram', 'u-outbox', now()) on conflict do nothing",
        tenant,
        cliente,
    )
    clave = "cc:" + str(
        await db_superuser.fetchval(
            "select id from public.canales_cliente where tenant_id = $1 and canal = 'telegram'", tenant
        )
    )
    bot = await conectar_como("bot_service")
    tx = bot.transaction()
    await tx.start()
    id_ = await _encolar(bot, tenant, cliente)
    await tx.commit()

    job_a = await conectar_como("scheduler_job")
    job_b = await conectar_como("scheduler_job")
    registrar = "select public.registrar_entrega_outbox($1, $2, $3, $4)"
    tx_a, tx_b = job_a.transaction(), job_b.transaction()
    await tx_a.start()
    await job_a.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
    assert await job_a.fetchval(registrar, id_, clave, "sent", None) is True
    await tx_b.start()
    await job_b.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
    # B espera el candado de la fila hasta que A confirma, y entonces ve el canal ya entregado
    tarea_b = asyncio.ensure_future(job_b.fetchval(registrar, id_, clave, "failed", "x"))
    await asyncio.sleep(0.5)
    assert not tarea_b.done()
    await tx_a.commit()
    assert await asyncio.wait_for(tarea_b, 10) is False
    await tx_b.commit()
    estado = await db_superuser.fetchval(
        "select entregas -> $2 ->> 'estado' from public.notification_outbox where id = $1", id_, clave
    )
    assert estado == "sent"
