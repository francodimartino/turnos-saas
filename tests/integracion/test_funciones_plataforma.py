"""Funciones SECURITY DEFINER de plataforma con conexiones reales de cada rol de login.

Lo que depende de `session_user` (el actor de `write_audit_log`, el chequeo de
`store_tenant_integration`) no se puede probar con `set role` en pgTAP: acá el caller es una
conexión asyncpg autenticada como el rol, tal como se conectan los backends. Los datos se
confirman con el superusuario de test y se limpian al terminar (audit_log es solo-agregar:
el trigger se desactiva únicamente en la limpieza).
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def dos_tenants(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        tenant_a,
        f"it-fn-a-{sufijo}",
        tenant_b,
        f"it-fn-b-{sufijo}",
    )
    try:
        yield tenant_a, tenant_b
    finally:
        tenants = [tenant_a, tenant_b]
        async with db_superuser.transaction():
            # audit_log es solo-agregar: el trigger se apaga solo acá, dentro de la transacción de limpieza
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            secretos = await db_superuser.fetch(
                "select secret_id from public.tenant_integrations "
                "where tenant_id = any($1::uuid[]) and secret_id is not null",
                tenants,
            )
            await db_superuser.execute(
                "delete from public.tenant_integrations where tenant_id = any($1::uuid[])", tenants
            )
            await db_superuser.execute(
                "delete from vault.secrets where id = any($1::uuid[])", [fila["secret_id"] for fila in secretos]
            )
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
# write_audit_log: el actor se deriva de session_user
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rol", "actor_esperado"),
    [("bot_service", "bot"), ("dashboard_service", "staff")],
)
async def test_write_audit_log_deriva_el_actor_del_rol_con_tenant_del_contexto(
    conectar_como, db_superuser, dos_tenants, rol, actor_esperado
):
    tenant_a, _ = dos_tenants
    actor = uuid.uuid4()
    conn = await conectar_como(rol)
    tx = await _contexto(conn, tenant_a, actor)
    try:
        await conn.fetchval(
            "select public.write_audit_log($1, $2, $3, $4)",
            f"it_{rol}",
            "end_customers",
            uuid.uuid4(),
            json.dumps({"campo": "valor"}),
        )
    finally:
        await tx.commit()
    fila = await db_superuser.fetchrow(
        "select tenant_id, actor_type, actor_id, entity, metadata from public.audit_log where action = $1",
        f"it_{rol}",
    )
    assert fila["tenant_id"] == tenant_a
    assert fila["actor_type"] == actor_esperado
    assert fila["actor_id"] == actor
    assert fila["entity"] == "end_customers"
    assert json.loads(fila["metadata"]) == {"campo": "valor"}


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_write_audit_log_exige_contexto_de_tenant_a_roles_de_tenant(conectar_como, dos_tenants, rol):
    conn = await conectar_como(rol)
    tx = await _contexto(conn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="contexto de tenant"):
            await conn.fetchval("select public.write_audit_log('sin_contexto')")
    finally:
        await tx.rollback()


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_write_audit_log_no_deja_escribir_en_otro_tenant(conectar_como, dos_tenants, rol):
    tenant_a, tenant_b = dos_tenants
    conn = await conectar_como(rol)
    tx = await _contexto(conn, tenant_a)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="otro tenant"):
            await conn.fetchval("select public.write_audit_log('cruzado', null, null, null, $1)", tenant_b)
    finally:
        await tx.rollback()


async def test_write_audit_log_de_platform_admin_acepta_tenant_explicito_o_ninguno(
    conectar_como, db_superuser, dos_tenants
):
    tenant_a, _ = dos_tenants
    conn = await conectar_como("platform_admin")
    await conn.fetchval("select public.write_audit_log('it_pa_con_tenant', 'tenants', $1, null, $1)", tenant_a)
    await conn.fetchval("select public.write_audit_log('it_pa_global', 'platform_settings')")
    try:
        con_tenant = await db_superuser.fetchrow(
            "select tenant_id, actor_type from public.audit_log where action = 'it_pa_con_tenant'"
        )
        global_ = await db_superuser.fetchrow(
            "select tenant_id, actor_type from public.audit_log where action = 'it_pa_global'"
        )
        assert (con_tenant["tenant_id"], con_tenant["actor_type"]) == (tenant_a, "platform_admin")
        assert (global_["tenant_id"], global_["actor_type"]) == (None, "platform_admin")
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where action = 'it_pa_global'")
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")


async def test_write_audit_log_de_purge_job_queda_como_system(conectar_como, db_superuser, dos_tenants):
    tenant_a, _ = dos_tenants
    conn = await conectar_como("purge_job")
    await conn.fetchval("select public.write_audit_log('it_calendar_scrub_failed', 'tenants', $1, null, $1)", tenant_a)
    actor = await db_superuser.fetchval(
        "select actor_type from public.audit_log where action = 'it_calendar_scrub_failed'"
    )
    assert actor == "system"


@pytest.mark.parametrize("rol", ["scheduler_job", "platform_admin_readonly"])
async def test_write_audit_log_rechaza_roles_no_listados(conectar_como, rol):
    conn = await conectar_como(rol)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetchval("select public.write_audit_log('no_listado')")


async def test_audit_log_sigue_siendo_solo_agregar_para_los_roles_de_aplicacion(conectar_como, dos_tenants):
    tenant_a, _ = dos_tenants
    conn = await conectar_como("dashboard_service")
    tx = await _contexto(conn, tenant_a)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("insert into public.audit_log (actor_type, action) values ('staff', 'directo')")
    finally:
        await tx.rollback()


# ---------------------------------------------------------------------------
# store_tenant_integration: solo platform_admin (caller real)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rol", ["bot_service", "dashboard_service", "platform_admin_readonly", "purge_job", "scheduler_job"]
)
async def test_store_tenant_integration_falla_para_cualquier_rol_distinto_de_platform_admin(
    conectar_como, dos_tenants, rol
):
    tenant_a, _ = dos_tenants
    conn = await conectar_como(rol)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetchval(
            "select public.store_tenant_integration($1, 'telegram_bot', $2, 'secreto-que-no-debe-guardarse')",
            tenant_a,
            json.dumps({"webhook_key": f"k-{uuid.uuid4()}"}),
        )


async def test_store_tenant_integration_guarda_en_vault_y_solo_referencia_el_secret(
    conectar_como, db_superuser, dos_tenants
):
    tenant_a, _ = dos_tenants
    secreto = json.dumps({"bot_token": "tok-it-123", "webhook_secret": "ws-it-456"})
    config = json.dumps({"webhook_key": f"it-{uuid.uuid4()}"})
    admin = await conectar_como("platform_admin")

    integracion = await admin.fetchval(
        "select public.store_tenant_integration($1, 'telegram_bot', $2, $3)", tenant_a, config, secreto
    )

    fila = await db_superuser.fetchrow("select * from public.tenant_integrations where id = $1", integracion)
    assert fila["tenant_id"] == tenant_a
    assert fila["integration_type"] == "telegram_bot"
    assert fila["professional_id"] is None
    assert fila["secret_id"] is not None
    # el secret no está en ninguna columna de la fila (config incluida)
    assert "tok-it-123" not in json.dumps({k: str(v) for k, v in dict(fila).items()})
    # y sí en Vault
    en_vault = await db_superuser.fetchval(
        "select decrypted_secret from vault.decrypted_secrets where id = $1", fila["secret_id"]
    )
    assert en_vault == secreto
    # la credencial en claro tampoco quedó en audit_log
    metadata = await db_superuser.fetchval(
        "select metadata::text from public.audit_log where action = 'store_tenant_integration' and tenant_id = $1",
        tenant_a,
    )
    assert metadata is not None
    assert "tok-it-123" not in metadata
    actor = await db_superuser.fetchval(
        "select actor_type from public.audit_log where action = 'store_tenant_integration' and tenant_id = $1",
        tenant_a,
    )
    assert actor == "platform_admin"


async def test_store_tenant_integration_vuelve_a_guardar_sobre_la_misma_integracion(
    conectar_como, db_superuser, dos_tenants
):
    tenant_a, _ = dos_tenants
    config = json.dumps({"webhook_key": f"it-{uuid.uuid4()}"})
    admin = await conectar_como("platform_admin")
    primera = await admin.fetchval(
        "select public.store_tenant_integration($1, 'telegram_bot', $2, 'v1')", tenant_a, config
    )
    segunda = await admin.fetchval(
        "select public.store_tenant_integration($1, 'telegram_bot', $2, 'v2')", tenant_a, config
    )
    assert primera == segunda
    assert (
        await db_superuser.fetchval("select count(*) from public.tenant_integrations where tenant_id = $1", tenant_a)
        == 1
    )
    nuevo = await db_superuser.fetchval(
        "select ds.decrypted_secret from public.tenant_integrations ti "
        "join vault.decrypted_secrets ds on ds.id = ti.secret_id where ti.id = $1",
        primera,
    )
    assert nuevo == "v2"


async def test_store_tenant_integration_rechaza_integraciones_por_profesional_y_tenants_inexistentes(
    conectar_como, dos_tenants
):
    tenant_a, _ = dos_tenants
    admin = await conectar_como("platform_admin")
    with pytest.raises(asyncpg.PostgresError):
        await admin.fetchval("select public.store_tenant_integration($1, 'google_calendar', '{}', 's')", tenant_a)
    with pytest.raises(asyncpg.PostgresError):
        await admin.fetchval("select public.store_tenant_integration($1, 'telegram_bot', '{}', 's')", uuid.uuid4())
    with pytest.raises(asyncpg.PostgresError):
        await admin.fetchval("select public.store_tenant_integration($1, 'telegram_bot', '{}', '')", tenant_a)


async def test_el_bot_obtiene_el_secret_de_su_tenant_y_no_el_de_otro(conectar_como, dos_tenants):
    tenant_a, tenant_b = dos_tenants
    admin = await conectar_como("platform_admin")
    integracion_a = await admin.fetchval(
        "select public.store_tenant_integration($1, 'telegram_bot', $2, 'secreto-a')",
        tenant_a,
        json.dumps({"webhook_key": f"it-{uuid.uuid4()}"}),
    )
    bot = await conectar_como("bot_service")
    tx = await _contexto(bot, tenant_a)
    try:
        assert await bot.fetchval("select public.find_integration('telegram_bot')") == integracion_a
        assert await bot.fetchval("select public.get_integration_secret($1)", integracion_a) == "secreto-a"
    finally:
        await tx.rollback()
    tx = await _contexto(bot, tenant_b)
    try:
        assert await bot.fetchval("select public.find_integration('telegram_bot')") is None
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="otro tenant"):
            await bot.fetchval("select public.get_integration_secret($1)", integracion_a)
    finally:
        await tx.rollback()


async def test_los_roles_de_aplicacion_no_leen_vault_ni_tenant_integrations_directo(conectar_como, dos_tenants):
    tenant_a, _ = dos_tenants
    for rol in ("bot_service", "dashboard_service", "scheduler_job"):
        conn = await conectar_como(rol)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("select decrypted_secret from vault.decrypted_secrets")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("select secret_id from public.tenant_integrations")
