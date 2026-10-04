"""Funciones del Cron Job de vencimiento con la conexión real de `scheduler_job`.

Complementa el pgTAP (supabase/tests/turnos_50_jobs_vencimiento.sql): acá el caller es una
conexión asyncpg autenticada como `scheduler_job` (session_user real), tal como se conecta el
job. Los datos se confirman con el superusuario de test y se borran al terminar.
"""

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def escenario(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    d = {k: uuid.uuid4() for k in ("tenant", "prof", "servicio", "cliente", "pack", "vencido", "futuro")}
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'J', $2)", d["tenant"], f"it-jobs-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
    )
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'S', 30, 10)",
        d["servicio"],
        d["tenant"],
    )
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
        "values ($1, $2, 'telegram', 'u-it')",
        d["cliente"],
        d["tenant"],
    )
    await db_superuser.execute(
        "insert into public.booking_packs "
        "(id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) "
        "values ($1, $2, $3, $4, 5, 1, 'manual_grant')",
        d["pack"],
        d["tenant"],
        d["cliente"],
        d["servicio"],
    )
    insertar = (
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at, booking_pack_id) values ($1, $2, $3, $4, $5, "
        "tstzrange($6::timestamptz, $6::timestamptz + interval '30 minutes'), 'pending', "
        "now() + ($7::text || ' minutes')::interval, $8)"
    )
    await db_superuser.execute(
        insertar, d["vencido"], d["tenant"], d["cliente"], d["servicio"], d["prof"],
        datetime.fromisoformat("2031-01-01T10:00:00+00:00"), "-5", d["pack"],
    )  # fmt: skip
    await db_superuser.execute(
        insertar, d["futuro"], d["tenant"], d["cliente"], d["servicio"], d["prof"],
        datetime.fromisoformat("2031-01-02T10:00:00+00:00"), "60", None,
    )  # fmt: skip
    try:
        yield d
    finally:
        t = d["tenant"]
        for tabla in (
            "notifications_log",
            "bookings",
            "booking_packs",
            "bot_sessions",
            "professionals",
            "services",
            "end_customers",
        ):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = $1", t)
        await db_superuser.execute("delete from public.tenants where id = $1", t)


async def test_expire_pending_bookings_como_scheduler_job(conectar_como, db_superuser, escenario):
    d = escenario
    conn = await conectar_como("scheduler_job")
    filas = await conn.fetch("select * from public.expire_pending_bookings()")
    propias = [f for f in filas if f["tenant_id"] == d["tenant"]]
    assert len(propias) == 1
    fila = propias[0]
    assert fila["booking_id"] == d["vencido"]
    assert (fila["channel"], fila["channel_user_id"], fila["notify"]) == ("telegram", "u-it", True)
    assert fila["end_customer_id"] == d["cliente"]

    estados = {
        r["id"]: r["status"]
        for r in await db_superuser.fetch("select id, status from public.bookings where tenant_id = $1", d["tenant"])
    }
    assert estados == {d["vencido"]: "cancelled", d["futuro"]: "pending"}
    usadas = await db_superuser.fetchval("select used_sessions from public.booking_packs where id = $1", d["pack"])
    assert usadas == 0

    # idempotente: la sesión del pack no se devuelve dos veces
    otra = await conn.fetch("select * from public.expire_pending_bookings()")
    assert [f for f in otra if f["tenant_id"] == d["tenant"]] == []
    assert await db_superuser.fetchval("select used_sessions from public.booking_packs where id = $1", d["pack"]) == 0


async def test_prune_bot_sessions_como_scheduler_job(conectar_como, db_superuser, escenario):
    d = escenario
    for usuario, minutos in (("viejo", 120), ("reciente", 5)):
        await db_superuser.execute(
            "insert into public.bot_sessions (tenant_id, channel, channel_user_id, updated_at) "
            "values ($1, 'telegram', $2, now() - ($3::text || ' minutes')::interval)",
            d["tenant"], usuario, str(minutos),
        )  # fmt: skip
    conn = await conectar_como("scheduler_job")
    borradas = await conn.fetchval("select public.prune_bot_sessions()")
    assert borradas >= 1
    restantes = await db_superuser.fetch(
        "select channel_user_id from public.bot_sessions where tenant_id = $1", d["tenant"]
    )
    assert [r["channel_user_id"] for r in restantes] == ["reciente"]


async def test_record_notification_attempt_exige_contexto_y_tenant_del_cliente(conectar_como, db_superuser, escenario):
    d = escenario
    conn = await conectar_como("scheduler_job")
    llamar = "select public.record_notification_attempt($1, 'telegram', 'turno_vencido', 'pending', 0, null)"

    tx = conn.transaction()
    await tx.start()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval(llamar, d["cliente"])
    finally:
        await tx.rollback()

    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(uuid.uuid4()))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval(llamar, d["cliente"])
    finally:
        await tx.rollback()

    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(d["tenant"]))
        notificacion = await conn.fetchval(llamar, d["cliente"])
        await conn.fetchval(
            "select public.record_notification_attempt($1, 'telegram', 'turno_vencido', 'failed', 3, 'timeout', $2)",
            d["cliente"], notificacion,
        )  # fmt: skip
    finally:
        await tx.commit()
    fila = await db_superuser.fetchrow(
        "select status, retry_count, last_error from public.notifications_log where id = $1", notificacion
    )
    assert (fila["status"], fila["retry_count"], fila["last_error"]) == ("failed", 3, "timeout")


async def test_scheduler_job_no_lee_tablas_con_conexion_real(conectar_como, escenario):
    conn = await conectar_como("scheduler_job")
    for tabla in ("bookings", "bot_sessions", "notifications_log", "end_customers"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"select * from public.{tabla}")


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service", "platform_admin", "purge_job"])
async def test_otros_roles_no_ejecutan_las_funciones_del_job(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    for sentencia in ("select * from public.expire_pending_bookings()", "select public.prune_bot_sessions()"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(sentencia)
