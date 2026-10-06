"""get_tenant_staff_identity con conexiones reales de cada rol de login (depende de session_user)."""

import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def escenario(db_superuser):
    s = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4(), uuid.uuid4()
    cuenta_a, cuenta_b = uuid.uuid4(), uuid.uuid4()
    tu_a, tu_pend, tu_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        a, f"it-id-a-{s}", b, f"it-id-b-{s}",
    )
    await db_superuser.execute(
        "insert into auth.users (id, email, raw_user_meta_data) values "
        "($1, $2, '{\"full_name\": \"Ana Prueba\"}'::jsonb), ($3, $4, '{}'::jsonb)",
        cuenta_a, f"ana-{s}@ejemplo.invalid", cuenta_b, f"bruno-{s}@ejemplo.invalid",
    )
    await db_superuser.execute(
        "insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner) values ($1, $2, $3, true)",
        tu_a, a, cuenta_a,
    )
    await db_superuser.execute(
        "insert into public.tenant_users (id, tenant_id, pending_invite_email) values ($1, $2, $3)",
        tu_pend, a, f"pendiente-{s}@ejemplo.invalid",
    )
    await db_superuser.execute(
        "insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner) values ($1, $2, $3, true)",
        tu_b, b, cuenta_b,
    )
    try:
        yield {"s": s, "a": a, "b": b, "tu_a": tu_a, "tu_pend": tu_pend, "tu_b": tu_b}
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("set local session_replication_role = replica")
            await db_superuser.execute("delete from public.tenant_users where tenant_id = any($1::uuid[])", [a, b])
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", [a, b])
            await db_superuser.execute("delete from auth.users where id = any($1::uuid[])", [cuenta_a, cuenta_b])


async def _llamar(conn, pedido, contexto=None):
    tx = conn.transaction()
    await tx.start()
    try:
        if contexto is not None:
            await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(contexto))
        return await conn.fetch("select * from public.get_tenant_staff_identity($1)", pedido)
    finally:
        await tx.rollback()


async def test_dashboard_ve_nombre_email_y_pendiente_de_su_tenant(conectar_como, escenario):
    conn = await conectar_como("dashboard_service")
    filas = {f["tenant_user_id"]: f for f in await _llamar(conn, escenario["a"], escenario["a"])}
    assert set(filas) == {escenario["tu_a"], escenario["tu_pend"]}
    assert filas[escenario["tu_a"]]["email"] == f"ana-{escenario['s']}@ejemplo.invalid"
    assert filas[escenario["tu_a"]]["nombre"] == "Ana Prueba"
    assert filas[escenario["tu_pend"]]["email"] == f"pendiente-{escenario['s']}@ejemplo.invalid"
    assert filas[escenario["tu_pend"]]["nombre"] is None


async def test_dashboard_sin_nombre_en_metadata_devuelve_null(conectar_como, escenario):
    conn = await conectar_como("dashboard_service")
    filas = await _llamar(conn, escenario["b"], escenario["b"])
    assert len(filas) == 1 and filas[0]["nombre"] is None


async def test_dashboard_no_puede_pedir_un_tenant_ajeno(conectar_como, escenario):
    conn = await conectar_como("dashboard_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _llamar(conn, escenario["b"], escenario["a"])


async def test_dashboard_sin_contexto_de_tenant_es_rechazado(conectar_como, escenario):
    conn = await conectar_como("dashboard_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _llamar(conn, escenario["a"])


async def test_platform_admin_lee_cualquier_tenant(conectar_como, escenario):
    conn = await conectar_como("platform_admin")
    assert len(await _llamar(conn, escenario["a"])) == 2
    assert len(await _llamar(conn, escenario["b"])) == 1


@pytest.mark.parametrize(
    "rol", ["bot_service", "scheduler_job", "purge_job", "platform_admin_readonly"]
)
async def test_rol_no_autorizado_es_rechazado(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _llamar(conn, escenario["a"], escenario["a"])
