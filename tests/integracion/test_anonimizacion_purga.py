"""Anonimización y purga de tenant con conexiones reales de cada rol de login (§3.2, §3.5, §2.2).

Lo que depende de `session_user` (el actor de `audit_log`, el rechazo de roles no listados, el
caller `purge_job` de la cascada) no se puede probar con `set role` en pgTAP: acá cada caller es una
conexión asyncpg autenticada como el rol, tal como se conectan los backends. Los datos se confirman
con el superusuario de test y se limpian al terminar (audit_log es solo-agregar: el trigger se
desactiva únicamente en la limpieza).
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CLAVE_HMAC = "channel_user_hmac_key"


@pytest.fixture
async def clave_hmac(db_superuser):
    """Siembra la clave HMAC de Vault si no estaba (el script de setup real la pone una vez por proyecto)."""
    existente = await db_superuser.fetchval("select id from vault.secrets where name = $1", CLAVE_HMAC)
    creada = None
    if existente is None:
        creada = await db_superuser.fetchval("select vault.create_secret($1, $2)", "clave-de-prueba-integracion", CLAVE_HMAC)
    try:
        yield
    finally:
        if creada is not None:
            await db_superuser.execute("delete from vault.secrets where id = $1", creada)


@pytest.fixture
async def escenario(db_superuser, clave_hmac):
    """Dos tenants activos (A y B) con un end_customer cada uno, una bot_session y una notificación fallida."""
    sufijo = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    cliente_a, cliente_b = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        tenant_a,
        f"it-an-a-{sufijo}",
        tenant_b,
        f"it-an-b-{sufijo}",
    )
    for tenant, cliente, marca in ((tenant_a, cliente_a, "a"), (tenant_b, cliente_b, "b")):
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, extra_data) "
            "values ($1, $2, 'telegram', $3, '+5491100000000', 'Nombre', '{\"k\": 1}')",
            cliente,
            tenant,
            f"tg-{marca}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.bot_sessions (tenant_id, channel, channel_user_id) values ($1, 'telegram', $2)",
            tenant,
            f"tg-{marca}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.notifications_log (tenant_id, end_customer_id, channel, status, last_error) "
            "values ($1, $2, 'telegram', 'failed', 'error del proveedor')",
            tenant,
            cliente,
        )
    tenants = [tenant_a, tenant_b]
    try:
        yield {"a": tenant_a, "b": tenant_b, "cliente_a": cliente_a, "cliente_b": cliente_b, "sufijo": sufijo}
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            secretos = await db_superuser.fetch(
                "select secret_id from public.tenant_integrations where tenant_id = any($1::uuid[]) "
                "and secret_id is not null",
                tenants,
            )
            await db_superuser.execute("delete from public.notifications_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.bot_sessions where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.end_customers where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.tenant_integrations where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute(
                "delete from vault.secrets where id = any($1::uuid[])", [fila["secret_id"] for fila in secretos]
            )
            await db_superuser.execute("delete from public.inbound_messages where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


async def _contexto(conn, tenant_id=None, actor_id=None):
    """Abre una transacción y setea el contexto como lo hace platform_tenancy."""
    tx = conn.transaction()
    await tx.start()
    if tenant_id is not None:
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    if actor_id is not None:
        await conn.execute("select set_config('app.current_actor_id', $1, true)", str(actor_id))
    return tx


# ---------------------------------------------------------------------------
# anonymize_end_customer: actor derivado de session_user, chequeo de tenant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("rol", "actor_esperado"), [("bot_service", "bot"), ("dashboard_service", "staff")])
async def test_anonimizar_deriva_el_actor_del_rol_y_audita_sin_valores(
    conectar_como, db_superuser, escenario, rol, actor_esperado
):
    actor = uuid.uuid4()
    conn = await conectar_como(rol)
    tx = await _contexto(conn, escenario["a"], actor)
    try:
        await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_a"])
    finally:
        await tx.commit()

    fila = await db_superuser.fetchrow(
        "select phone, name, extra_data, channel_user_id, channel_user_hash, deleted_at "
        "from public.end_customers where id = $1",
        escenario["cliente_a"],
    )
    assert fila["phone"] is None and fila["name"] is None and fila["channel_user_id"] is None
    assert json.loads(fila["extra_data"]) == {}
    assert fila["channel_user_hash"] and len(fila["channel_user_hash"]) == 64
    assert fila["deleted_at"] is not None
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.bot_sessions where tenant_id = $1", escenario["a"]
        )
        == 0
    )
    assert (
        await db_superuser.fetchval(
            "select last_error from public.notifications_log where end_customer_id = $1", escenario["cliente_a"]
        )
        is None
    )
    audit = await db_superuser.fetchrow(
        "select actor_type, actor_id, tenant_id, metadata::text as metadata "
        "from public.audit_log where action = 'anonymize_end_customer' and entity_id = $1",
        escenario["cliente_a"],
    )
    assert audit["actor_type"] == actor_esperado
    assert audit["actor_id"] == actor
    assert audit["tenant_id"] == escenario["a"]
    assert "5491100000000" not in audit["metadata"] and "Nombre" not in audit["metadata"]
    # el cliente de B no se tocó
    assert await db_superuser.fetchval("select phone from public.end_customers where id = $1", escenario["cliente_b"])


async def test_anonimizar_es_idempotente_y_conserva_el_hash(conectar_como, db_superuser, escenario):
    conn = await conectar_como("dashboard_service")
    for _ in range(2):
        tx = await _contexto(conn, escenario["a"])
        try:
            await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_a"])
        finally:
            await tx.commit()
        fila = await db_superuser.fetchrow(
            "select channel_user_hash, channel_user_id from public.end_customers where id = $1",
            escenario["cliente_a"],
        )
        assert fila["channel_user_id"] is None
        if "primero" not in escenario:
            escenario["primero"] = fila["channel_user_hash"]
    assert fila["channel_user_hash"] == escenario["primero"]


async def test_bot_con_tenant_a_no_anonimiza_un_cliente_de_b(conectar_como, db_superuser, escenario):
    conn = await conectar_como("bot_service")
    tx = await _contexto(conn, escenario["a"])
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="tenant mismatch"):
            await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_b"])
    finally:
        await tx.rollback()
    assert await db_superuser.fetchval("select phone from public.end_customers where id = $1", escenario["cliente_b"])


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_anonimizar_sin_tenant_en_contexto_falla(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    tx = await _contexto(conn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="tenant mismatch"):
            await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_a"])
    finally:
        await tx.rollback()


@pytest.mark.parametrize("rol", ["purge_job", "scheduler_job", "platform_admin_readonly"])
async def test_anonimizar_rol_no_listado_falla(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    tx = await _contexto(conn, escenario["a"])
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
            await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_a"])
    finally:
        await tx.rollback()


async def test_platform_admin_cruza_tenants_y_queda_como_platform_admin(conectar_como, db_superuser, escenario):
    conn = await conectar_como("platform_admin")
    tx = await _contexto(conn)  # sin contexto de tenant
    try:
        await conn.execute("select public.anonymize_end_customer($1)", escenario["cliente_b"])
    finally:
        await tx.commit()
    assert (
        await db_superuser.fetchval(
            "select actor_type from public.audit_log where action = 'anonymize_end_customer' and entity_id = $1",
            escenario["cliente_b"],
        )
        == "platform_admin"
    )


# ---------------------------------------------------------------------------
# Cascada de purga como purge_job (sin grants de tabla)
# ---------------------------------------------------------------------------


async def test_cascada_de_purga_como_purge_job(conectar_como, db_superuser, escenario):
    tenant = escenario["a"]
    slug_original = await db_superuser.fetchval("select slug from public.tenants where id = $1", tenant)
    secreto = await db_superuser.fetchval(
        "select vault.create_secret($1, $2)", "secret-integracion", f"it-purga-{escenario['sufijo']}"
    )
    await db_superuser.execute(
        "insert into public.tenant_integrations (tenant_id, integration_type, secret_id, config) "
        "values ($1, 'mercadopago', $2, '{}')",
        tenant,
        secreto,
    )
    await db_superuser.execute(
        "update public.tenants set status = 'inactive' where id = $1", tenant
    )
    await db_superuser.execute(
        "update public.tenants set deactivated_at = now() - interval '100 days' where id = $1", tenant
    )
    await db_superuser.execute(
        "insert into public.inbound_messages (tenant_id, channel, external_message_id, received_at) "
        "values ($1, 'telegram', 'it-viejo', now() - interval '9 days'), ($1, 'telegram', 'it-nuevo', now())",
        tenant,
    )

    conn = await conectar_como("purge_job")

    due = await conn.fetch("select tenant_id, days_overdue from public.list_tenants_due_for_purge()")
    atraso = {fila["tenant_id"]: fila["days_overdue"] for fila in due}
    assert atraso[tenant] == 10
    assert escenario["b"] not in atraso  # activo

    with pytest.raises(asyncpg.CheckViolationError, match="purga rechazada"):
        await conn.fetch("select * from public.purge_tenant_data($1)", escenario["b"])

    auth_ids = await conn.fetch("select * from public.purge_tenant_data($1)", tenant)
    assert auth_ids == []  # sin tenant_users en este escenario

    fila = await db_superuser.fetchrow(
        "select phone, name, channel_user_id, channel_user_hash from public.end_customers where id = $1",
        escenario["cliente_a"],
    )
    assert fila["phone"] is None and fila["name"] is None and fila["channel_user_id"] is None
    assert fila["channel_user_hash"]
    assert await db_superuser.fetchval("select count(*) from vault.secrets where id = $1", secreto) == 0
    actores = await db_superuser.fetch(
        "select distinct actor_type from public.audit_log where entity_id = $1 and action = 'anonymize_end_customer'",
        escenario["cliente_a"],
    )
    assert [a["actor_type"] for a in actores] == ["system"]  # session_user sigue siendo purge_job (anidada)
    pasos = await db_superuser.fetch(
        "select metadata ->> 'step' as paso from public.audit_log "
        "where tenant_id = $1 and action = 'tenant_purge_step'",
        tenant,
    )
    assert {p["paso"] for p in pasos} == {
        "anonymize_end_customers",
        "delete_bot_sessions",
        "delete_vault_secrets",
        "collect_auth_users",
    }

    # idempotente
    assert await conn.fetch("select * from public.purge_tenant_data($1)", tenant) == []

    nuevo_slug = await conn.fetchval("select public.mark_tenant_purged($1)", tenant)
    assert nuevo_slug.startswith(f"{slug_original}-purged-")
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", tenant) == "purged"
    assert await conn.fetchval("select public.mark_tenant_purged($1)", tenant) == nuevo_slug

    borradas = await conn.fetchval("select public.prune_inbound_messages()")
    assert borradas >= 1
    restantes = await db_superuser.fetch(
        "select external_message_id from public.inbound_messages where tenant_id = $1", tenant
    )
    assert [r["external_message_id"] for r in restantes] == ["it-nuevo"]


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service", "platform_admin", "scheduler_job"])
async def test_otros_roles_no_ejecutan_la_purga(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    for consulta, args in (
        ("select * from public.list_tenants_due_for_purge()", ()),
        ("select * from public.purge_tenant_data($1)", (escenario["a"],)),
        ("select public.mark_tenant_purged($1)", (escenario["a"],)),
        ("select public.prune_inbound_messages()", ()),
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
            await conn.fetch(consulta, *args)
