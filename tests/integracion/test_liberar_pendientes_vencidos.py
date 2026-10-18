"""release_expired_pending_overlaps con la conexion real de `bot_service` y reservas simultaneas.

Complementa el pgTAP (supabase/tests/turnos_51_liberar_pendientes_vencidos.sql): ahi se prueba la logica en una
sola transaccion; aca dos sesiones reales compiten por el horario de un pendiente vencido. Los datos se confirman
con el superusuario de test y se borran al terminar.
"""

import asyncio
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

_INICIO = datetime.fromisoformat("2031-04-01T10:00:00+00:00")
_FIN = datetime.fromisoformat("2031-04-01T10:30:00+00:00")

_LIBERAR = "select * from public.release_expired_pending_overlaps($1, tstzrange($2, $3))"
_BLOQUEAR = "select pg_advisory_xact_lock(hashtextextended('turnos_profesional:' || $1::text || ':' || $2::text, 0))"
_RESERVAR = (
    "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
    "values ($1, $2, $3, $4, tstzrange($5, $6), 'confirmed') returning id"
)


@pytest.fixture
async def escenario(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    d = {k: uuid.uuid4() for k in ("tenant", "prof", "servicio", "cliente1", "cliente2", "vencido")}
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'L', $2)", d["tenant"], f"it-lib-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
    )
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'S', 30, 10)",
        d["servicio"],
        d["tenant"],
    )
    for clave, usuario in (("cliente1", "u-uno"), ("cliente2", "u-dos")):
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
            "values ($1, $2, 'telegram', $3)",
            d[clave],
            d["tenant"],
            usuario,
        )
    await db_superuser.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'pending', "
        "now() - interval '5 minutes')",
        d["vencido"], d["tenant"], d["cliente1"], d["servicio"], d["prof"], _INICIO, _FIN,
    )  # fmt: skip
    try:
        yield d
    finally:
        t = d["tenant"]
        for tabla in ("notifications_log", "bookings", "professionals", "services", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = $1", t)
        await db_superuser.execute("delete from public.tenants where id = $1", t)


async def _con_tenant(conn, tenant_id):
    tx = conn.transaction()
    await tx.start()
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    return tx


async def test_libera_y_reserva_como_bot_service(conectar_como, db_superuser, escenario):
    d = escenario
    conn = await conectar_como("bot_service")
    tx = await _con_tenant(conn, d["tenant"])
    try:
        filas = await conn.fetch(_LIBERAR, d["prof"], _INICIO, _FIN)
        resumen = [
            (f["booking_id"], f["channel"], f["channel_user_id"], f["notify"], f["end_customer_id"]) for f in filas
        ]
        assert resumen == [(d["vencido"], "telegram", "u-uno", True, d["cliente1"])]
        assert [(f["starts_at"], f["service_name"]) for f in filas] == [(_INICIO, "S")]
        nuevo = await conn.fetchval(_RESERVAR, d["tenant"], d["cliente2"], d["servicio"], d["prof"], _INICIO, _FIN)
        assert nuevo is not None
    finally:
        await tx.commit()
    assert await db_superuser.fetchval("select status from public.bookings where id = $1", d["vencido"]) == "cancelled"


async def test_reserva_simultanea_una_sola_gana(conectar_como, db_superuser, escenario):
    """Dos sesiones liberan y reservan el mismo horario a la vez: se serializan por el lock del profesional.
    La primera libera y reserva; la segunda espera, no encuentra nada que liberar y recibe el 23P01 limpio
    (sin deadlock). El pendiente se libera una sola vez."""
    d = escenario
    a = await conectar_como("bot_service")
    b = await conectar_como("bot_service")
    tx_a = await _con_tenant(a, d["tenant"])
    tx_b = await _con_tenant(b, d["tenant"])
    try:
        liberadas_a = await a.fetch(_LIBERAR, d["prof"], _INICIO, _FIN)
        assert len(liberadas_a) == 1

        # B intenta lo mismo mientras A sostiene el lock: tiene que esperar
        tarea_b = asyncio.create_task(b.fetch(_LIBERAR, d["prof"], _INICIO, _FIN))
        await asyncio.sleep(1)
        assert not tarea_b.done(), "B debia esperar el lock por profesional de A"

        reserva_a = await a.fetchval(_RESERVAR, d["tenant"], d["cliente2"], d["servicio"], d["prof"], _INICIO, _FIN)
        assert reserva_a is not None
        await tx_a.commit()

        liberadas_b = await asyncio.wait_for(tarea_b, timeout=15)
        assert liberadas_b == [], "el pendiente ya lo libero A: B no debe volver a liberarlo"
        with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
            await b.fetchval(_RESERVAR, d["tenant"], d["cliente1"], d["servicio"], d["prof"], _INICIO, _FIN)
    finally:
        await tx_b.rollback()
    ids = await db_superuser.fetch(
        "select id from public.bookings where tenant_id = $1 and status <> 'cancelled'", d["tenant"]
    )
    assert [r["id"] for r in ids] == [reserva_a]


async def test_reserva_ya_tomada_el_lock_no_se_pisa_con_el_del_profesional(conectar_como, escenario):
    """La clave del lock es la misma que la de las altas del dashboard/bot: si otra sesion ya tiene el lock del
    profesional, la funcion espera (no lo ignora)."""
    d = escenario
    a = await conectar_como("bot_service")
    b = await conectar_como("bot_service")
    tx_a = await _con_tenant(a, d["tenant"])
    tx_b = await _con_tenant(b, d["tenant"])
    try:
        await a.execute(_BLOQUEAR, str(d["tenant"]), str(d["prof"]))
        tarea = asyncio.create_task(b.fetch(_LIBERAR, d["prof"], _INICIO, _FIN))
        await asyncio.sleep(1)
        assert not tarea.done()
        await tx_a.rollback()
        filas = await asyncio.wait_for(tarea, timeout=15)
        assert len(filas) == 1
    finally:
        await tx_b.rollback()


@pytest.mark.parametrize("rol", ["dashboard_service", "scheduler_job", "purge_job"])
async def test_otros_roles_sin_execute(conectar_como, escenario, rol):
    d = escenario
    conn = await conectar_como(rol)
    tx = await _con_tenant(conn, d["tenant"])
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.fetch(_LIBERAR, d["prof"], _INICIO, _FIN)
    finally:
        await tx.rollback()
