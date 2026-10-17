"""RLS y grants de las tablas de turnos con conexiones reales de cada rol de login.

Complementa tests pgTAP (supabase/tests/turnos_40_rls_aislamiento.sql): acá el caller es una
conexión asyncpg autenticada como el rol (session_user real), tal como se conectan los backends,
y se verifica que los triggers de la migración de dominio 1, que corren como invocador, funcionen
con los grants mínimos de cada rol. Los datos se confirman (commit) con el superusuario de test
y se borran al terminar.
"""

import uuid

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def escenario(db_superuser):
    """Dos tenants con profesional, servicio, cliente y un pack con una sesión usada en cada uno."""
    sufijo = uuid.uuid4().hex[:8]
    datos = {}
    for k in ("a", "b"):
        datos[k] = {
            "tenant": uuid.uuid4(),
            "prof": uuid.uuid4(),
            "servicio": uuid.uuid4(),
            "cliente": uuid.uuid4(),
            "pack": uuid.uuid4(),
        }
    for k, d in datos.items():
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            d["tenant"], k.upper(), f"it-trls-{k}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) "
            "values ($1, $2, 'S', 30, 10)",
            d["servicio"], d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
            "values ($1, $2, 'telegram', 'u')",
            d["cliente"], d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.booking_packs "
            "(id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) "
            "values ($1, $2, $3, $4, 5, 1, 'manual_grant')",
            d["pack"], d["tenant"], d["cliente"], d["servicio"],
        )
    try:
        yield datos
    finally:
        ids = [d["tenant"] for d in datos.values()]
        for tabla in ("bookings", "booking_packs", "professionals", "services", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", ids)


async def _con_tenant(conn, tenant_id):
    tx = conn.transaction()
    await tx.start()
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    return tx


_INSERT_BOOKING = (
    "insert into public.bookings "
    "(tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id) "
    "values ($1, $2, $3, $4, tstzrange('2030-01-01 10:00+00', '2030-01-01 10:30+00'), 'confirmed', $5) returning id"
)


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_reservar_y_cancelar_devuelve_la_sesion_del_pack(conectar_como, escenario, rol):
    """Los triggers de bookings (tenant desde el profesional, validar pack, devolver sesión) corren como
    invocador: el rol que escribe necesita SELECT sobre professionals y UPDATE(used_sessions) en booking_packs."""
    a = escenario["a"]
    conn = await conectar_como(rol)
    tx = await _con_tenant(conn, a["tenant"])
    try:
        booking_id = await conn.fetchval(
            _INSERT_BOOKING, a["tenant"], a["cliente"], a["servicio"], a["prof"], a["pack"]
        )
        resultado = await conn.execute("update public.bookings set status = 'cancelled' where id = $1", booking_id)
        usadas = await conn.fetchval("select used_sessions from public.booking_packs where id = $1", a["pack"])
    finally:
        await tx.rollback()
    assert resultado == "UPDATE 1"
    assert usadas == 0


async def test_bot_service_no_reserva_con_datos_de_otro_tenant(conectar_como, escenario):
    a, b = escenario["a"], escenario["b"]
    conn = await conectar_como("bot_service")
    tx = await _con_tenant(conn, a["tenant"])
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.fetchval(_INSERT_BOOKING, b["tenant"], b["cliente"], b["servicio"], b["prof"], None)
    finally:
        await tx.rollback()


async def test_bot_service_solo_puede_actualizar_used_sessions(conectar_como, escenario):
    a = escenario["a"]
    conn = await conectar_como("bot_service")
    tx = await _con_tenant(conn, a["tenant"])
    try:
        resultado = await conn.execute("update public.booking_packs set used_sessions = 2 where id = $1", a["pack"])
        assert resultado == "UPDATE 1"
    finally:
        await tx.rollback()
    for sentencia in (
        "update public.booking_packs set total_sessions = 99 where id = $1",
        "delete from public.booking_packs where id = $1",
        "delete from public.bookings where tenant_id = $1",
    ):
        tx = await _con_tenant(conn, a["tenant"])
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sentencia, a["pack"] if "booking_packs" in sentencia else a["tenant"])
        finally:
            await tx.rollback()


async def test_dashboard_service_no_borra_bookings_ni_packs(conectar_como, escenario):
    a = escenario["a"]
    conn = await conectar_como("dashboard_service")
    for tabla in ("bookings", "booking_packs"):
        tx = await _con_tenant(conn, a["tenant"])
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(f"delete from public.{tabla} where tenant_id = $1", a["tenant"])
        finally:
            await tx.rollback()


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_tenant_a_no_ve_las_tablas_de_turnos_del_tenant_b(conectar_como, escenario, rol):
    a, b = escenario["a"], escenario["b"]
    conn = await conectar_como(rol)
    tx = await _con_tenant(conn, a["tenant"])
    try:
        for tabla in ("professionals", "services", "booking_packs"):
            assert await conn.fetchval(f"select count(*) from public.{tabla} where tenant_id = $1", a["tenant"]) == 1
            assert await conn.fetchval(f"select count(*) from public.{tabla} where tenant_id = $1", b["tenant"]) == 0
    finally:
        await tx.rollback()


async def test_platform_admin_readonly_sin_contexto_no_ve_turnos(conectar_como, escenario):
    conn = await conectar_como("platform_admin_readonly")
    for tabla in ("bookings", "booking_packs", "professionals", "services"):
        assert await conn.fetchval(f"select count(*) from public.{tabla}") == 0, tabla


async def test_platform_admin_readonly_con_contexto_ve_solo_su_tenant_y_no_escribe(conectar_como, escenario):
    a, b = escenario["a"], escenario["b"]
    ids = [a["tenant"], b["tenant"]]
    conn = await conectar_como("platform_admin_readonly")
    tx = await _con_tenant(conn, a["tenant"])
    try:
        for tabla in ("booking_packs", "professionals"):
            sql = f"select count(*) from public.{tabla} where tenant_id = any($1::uuid[])"
            assert await conn.fetchval(sql, ids) == 1, tabla
            assert await conn.fetchval(f"select count(*) from public.{tabla} where tenant_id = $1", b["tenant"]) == 0
    finally:
        await tx.rollback()
    for sentencia in (
        "update public.booking_packs set used_sessions = 0 where tenant_id = $1",
        "delete from public.professionals where tenant_id = $1",
        "insert into public.services (tenant_id, name, duration_minutes, price) values ($1, 'x', 30, 1)",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sentencia, a["tenant"])


@pytest.mark.parametrize("rol", ["purge_job", "scheduler_job", "platform_admin"])
async def test_roles_sin_acceso_directo_a_datos_de_turnos(conectar_como, rol):
    conn = await conectar_como(rol)
    for tabla in ("bookings", "booking_packs", "professionals", "services"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"select 1 from public.{tabla}")
