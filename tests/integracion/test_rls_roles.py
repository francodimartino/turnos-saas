"""RLS y grants con conexiones reales de cada rol de login (no `set session authorization`).

Complementa los tests pgTAP: acá el caller es una conexión asyncpg autenticada como el rol,
tal como se conectan los backends. Los datos se confirman (commit) con el superusuario de test
y se borran al terminar.
"""

import uuid

import pytest

pytestmark = pytest.mark.integracion

# asyncpg se importa con importorskip: sin el driver (entorno local sin dependencias) se omite el módulo.
asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def dos_tenants(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        tenant_a,
        f"it-rls-a-{sufijo}",
        tenant_b,
        f"it-rls-b-{sufijo}",
    )
    await db_superuser.execute(
        "insert into public.end_customers (tenant_id, channel, channel_user_id) "
        "values ($1, 'telegram', 'ua'), ($2, 'telegram', 'ub')",
        tenant_a,
        tenant_b,
    )
    try:
        yield tenant_a, tenant_b
    finally:
        await db_superuser.execute(
            "delete from public.end_customers where tenant_id = any($1::uuid[])", [tenant_a, tenant_b]
        )
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", [tenant_a, tenant_b])


async def _con_tenant(conn, tenant_id):
    """Abre una transacción y setea el contexto como lo hace platform_tenancy."""
    tx = conn.transaction()
    await tx.start()
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    return tx


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_el_tenant_a_no_ve_filas_del_tenant_b(conectar_como, dos_tenants, rol):
    tenant_a, tenant_b = dos_tenants
    conn = await conectar_como(rol)
    tx = await _con_tenant(conn, tenant_a)
    try:
        propias = await conn.fetchval("select count(*) from public.end_customers where tenant_id = $1", tenant_a)
        ajenas = await conn.fetchval("select count(*) from public.end_customers where tenant_id = $1", tenant_b)
        tenants_visibles = await conn.fetch("select id from public.tenants")
    finally:
        await tx.rollback()
    assert propias == 1
    assert ajenas == 0
    assert [fila["id"] for fila in tenants_visibles] == [tenant_a]


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_sin_contexto_de_tenant_no_se_ve_nada(conectar_como, dos_tenants, rol):
    """En una conexión reutilizada el GUC queda vacío ('') y la policy debe devolver 0 filas, no error."""
    tenant_a, _ = dos_tenants
    conn = await conectar_como(rol)
    tx = await _con_tenant(conn, tenant_a)
    await tx.rollback()
    assert await conn.fetchval("select count(*) from public.end_customers") == 0


async def test_dashboard_service_no_puede_cambiar_el_status_del_tenant(conectar_como, dos_tenants):
    tenant_a, _ = dos_tenants
    conn = await conectar_como("dashboard_service")
    tx = await _con_tenant(conn, tenant_a)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("update public.tenants set status = 'suspended' where id = $1", tenant_a)
    finally:
        await tx.rollback()


async def test_dashboard_service_actualiza_solo_settings(conectar_como, dos_tenants):
    tenant_a, tenant_b = dos_tenants
    conn = await conectar_como("dashboard_service")
    tx = await _con_tenant(conn, tenant_a)
    try:
        propio = await conn.execute(
            'update public.tenants set settings = \'{"direccion": "Calle 1"}\' where id = $1', tenant_a
        )
        ajeno = await conn.execute(
            'update public.tenants set settings = \'{"direccion": "x"}\' where id = $1', tenant_b
        )
    finally:
        await tx.rollback()
    assert propio == "UPDATE 1"
    assert ajeno == "UPDATE 0"


async def test_bot_service_no_lee_tenant_integrations(conectar_como):
    conn = await conectar_como("bot_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select 1 from public.tenant_integrations")


async def test_dashboard_service_no_lee_secretos_de_tenant_integrations(conectar_como):
    conn = await conectar_como("dashboard_service")
    await conn.fetch("select id, tenant_id, integration_type from public.tenant_integrations")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select secret_id from public.tenant_integrations")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select config from public.tenant_integrations")


async def test_platform_admin_readonly_sin_contexto_no_ve_nada(conectar_como, dos_tenants):
    tenant_a, tenant_b = dos_tenants
    conn = await conectar_como("platform_admin_readonly")
    for tabla in ("end_customers", "tenant_users", "tenants"):
        assert await conn.fetchval(f"select count(*) from public.{tabla}") == 0, tabla


async def test_platform_admin_readonly_con_contexto_ve_solo_su_tenant_y_no_escribe(conectar_como, dos_tenants):
    tenant_a, tenant_b = dos_tenants
    conn = await conectar_como("platform_admin_readonly")
    tx = await _con_tenant(conn, tenant_a)
    try:
        sql = "select count(*) from public.end_customers where tenant_id = $1"
        assert await conn.fetchval(sql, tenant_a) == 1
        assert await conn.fetchval(sql, tenant_b) == 0
        assert await conn.fetchval("select count(*) from public.tenants where id = $1", tenant_b) == 0
    finally:
        await tx.rollback()
    for sentencia in (
        "insert into public.end_customers (tenant_id) values ($1)",
        "update public.end_customers set name = 'x' where tenant_id = $1",
        "delete from public.end_customers where tenant_id = $1",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sentencia, tenant_a)


@pytest.mark.parametrize("rol", ["purge_job", "scheduler_job"])
async def test_jobs_no_tienen_acceso_a_tablas(conectar_como, rol):
    conn = await conectar_como(rol)
    for tabla in ("tenants", "end_customers", "notifications_log", "platform_settings"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"select 1 from public.{tabla}")


@pytest.mark.parametrize("rol", ["anon", "authenticated", "service_role"])
async def test_roles_de_supabase_sin_grants_por_defecto(db_superuser, rol):
    privilegios = await db_superuser.fetchval(
        """
        select count(*) from pg_class c
        cross join unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
        where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
          and has_table_privilege($1, c.oid, p)
        """,
        rol,
    )
    assert privilegios == 0
