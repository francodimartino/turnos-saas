"""Alta y movimiento de turnos contra Postgres real, con el rol `dashboard_service` dentro de `tenant_tx`.

Cubre lo que la conexión simulada no puede: el exclusion constraint de verdad, que un choque no aborta la
transacción del canal (el savepoint), el movimiento del mismo turno y el consumo de la sesión de un pack.
"""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from platform_tenancy import crear_pool, tenant_tx

from turnos_dominio import PackNoDisponible, crear_turno, mover_turno

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
BASE = datetime.now(UTC).replace(microsecond=0, second=0, minute=0) + timedelta(days=40)
MEDIA_HORA = timedelta(minutes=30)


def _dsn(base: str, rol: str, password: str) -> str:
    p = urlsplit(base)
    host = p.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{host}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, ""))


@pytest.fixture
async def pool_dashboard(database_url, roles_password):
    pool = await crear_pool(
        _dsn(database_url, "dashboard_service", roles_password), min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def escenario(db_superuser):
    """Dos tenants, cada uno con cliente, servicio y profesional; el tenant A además con un pack de dos sesiones."""
    sufijo = uuid.uuid4().hex[:8]
    datos: dict[str, dict[str, uuid.UUID]] = {}
    for letra in ("a", "b"):
        d = {k: uuid.uuid4() for k in ("tenant", "cliente", "servicio", "profesional")}
        datos[letra] = d
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            d["tenant"],
            letra.upper(),
            f"it-alta-{letra}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
            "values ($1, $2, 'telegram', $3)",
            d["cliente"],
            d["tenant"],
            f"chat-{uuid.uuid4().hex[:8]}",
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) "
            "values ($1, $2, 'Corte', 30, 10)",
            d["servicio"],
            d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')",
            d["profesional"],
            d["tenant"],
        )
    a = datos["a"]
    a["pack"] = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin, "
        "payment_status) values ($1, $2, $3, $4, 2, 'manual_grant', 'not_required')",
        a["pack"],
        a["tenant"],
        a["cliente"],
        a["servicio"],
    )
    tenants = [datos["a"]["tenant"], datos["b"]["tenant"]]
    try:
        yield datos
    finally:
        for tabla in ("bookings", "booking_packs", "professionals", "services", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


def _alta(d: dict[str, uuid.UUID], inicio: datetime, **extra):
    return {
        "tenant_id": d["tenant"],
        "end_customer_id": d["cliente"],
        "servicio_id": d["servicio"],
        "profesional_id": d["profesional"],
        "inicio": inicio,
        "fin": inicio + MEDIA_HORA,
        "estado": "confirmed",
        **extra,
    }


async def _rango(db, booking_id):
    fila = await db.fetchrow(
        "select lower(time_range) as inicio, upper(time_range) as fin from public.bookings where id = $1", booking_id
    )
    return fila["inicio"], fila["fin"]


async def test_mover_cambia_el_rango_del_mismo_turno(pool_dashboard, escenario, db_superuser):
    a = escenario["a"]
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        turno = await crear_turno(conn, **_alta(a, BASE))
    nuevo = BASE + timedelta(hours=2)
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        resultado = await mover_turno(
            conn,
            tenant_id=a["tenant"],
            booking_id=turno.id,
            profesional_id=a["profesional"],
            inicio=nuevo,
            fin=nuevo + MEDIA_HORA,
        )
    assert resultado == "movido"
    assert await _rango(db_superuser, turno.id) == (nuevo, nuevo + MEDIA_HORA)
    assert await db_superuser.fetchval("select count(*) from public.bookings where tenant_id = $1", a["tenant"]) == 1


async def test_mover_a_un_horario_ocupado_es_choque_y_no_aborta_la_transaccion(pool_dashboard, escenario, db_superuser):
    a = escenario["a"]
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        primero = await crear_turno(conn, **_alta(a, BASE))
        segundo = await crear_turno(conn, **_alta(a, BASE + timedelta(hours=1)))
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        resultado = await mover_turno(
            conn,
            tenant_id=a["tenant"],
            booking_id=segundo.id,
            profesional_id=a["profesional"],
            inicio=BASE + timedelta(minutes=15),
            fin=BASE + timedelta(minutes=45),
        )
        # La transacción sigue utilizable: el choque se resolvió en el savepoint.
        assert await conn.fetchval("select 1") == 1
    assert resultado == "choque"
    assert await _rango(db_superuser, segundo.id) == (BASE + timedelta(hours=1), BASE + timedelta(hours=1, minutes=30))
    assert await _rango(db_superuser, primero.id) == (BASE, BASE + MEDIA_HORA)


async def test_mover_un_turno_inexistente_o_de_otro_tenant_es_no_existe(pool_dashboard, escenario):
    a, b = escenario["a"], escenario["b"]
    async with tenant_tx(pool_dashboard, b["tenant"]) as conn:
        ajeno = await crear_turno(conn, **_alta(b, BASE))
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        for booking_id in (uuid.uuid4(), ajeno.id):
            resultado = await mover_turno(
                conn,
                tenant_id=a["tenant"],
                booking_id=booking_id,
                profesional_id=a["profesional"],
                inicio=BASE + timedelta(hours=3),
                fin=BASE + timedelta(hours=3, minutes=30),
            )
            assert resultado == "no_existe"


async def test_crear_en_un_horario_ocupado_devuelve_none_y_la_transaccion_sigue_viva(pool_dashboard, escenario):
    a = escenario["a"]
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        assert await crear_turno(conn, **_alta(a, BASE)) is not None
        assert await crear_turno(conn, **_alta(a, BASE + timedelta(minutes=15))) is None
        assert await crear_turno(conn, **_alta(a, BASE + timedelta(hours=1))) is not None


async def test_crear_con_pack_consume_una_sesion_y_sin_sesiones_levanta_sin_insertar(
    pool_dashboard, escenario, db_superuser
):
    a = escenario["a"]
    for hora in (0, 1):
        async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
            turno = await crear_turno(conn, **_alta(a, BASE + timedelta(hours=hora), booking_pack_id=a["pack"]))
        assert (
            await db_superuser.fetchval("select booking_pack_id from public.bookings where id = $1", turno.id)
            == a["pack"]
        )
    assert await db_superuser.fetchval("select used_sessions from public.booking_packs where id = $1", a["pack"]) == 2
    with pytest.raises(PackNoDisponible):
        async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
            await crear_turno(conn, **_alta(a, BASE + timedelta(hours=2), booking_pack_id=a["pack"]))
    assert await db_superuser.fetchval("select count(*) from public.bookings where tenant_id = $1", a["tenant"]) == 2


async def test_un_choque_con_pack_devuelve_la_sesion(pool_dashboard, escenario, db_superuser):
    a = escenario["a"]
    async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
        await crear_turno(conn, **_alta(a, BASE))
        assert await crear_turno(conn, **_alta(a, BASE, booking_pack_id=a["pack"])) is None
    assert await db_superuser.fetchval("select used_sessions from public.booking_packs where id = $1", a["pack"]) == 0
