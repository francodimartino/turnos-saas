"""Cálculo de huecos contra Postgres real, leyendo como `bot_service` dentro de `tenant_tx`.

Incluye la propiedad central: todo hueco devuelto es reservable (el exclusion constraint
`bookings_sin_doble_booking` no lo rechaza).
"""

import uuid
from datetime import datetime
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pytest
from platform_tenancy import crear_pool, tenant_tx

from disponibilidad.consulta import calcular_huecos
from disponibilidad.huecos import Ventana

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

BA = ZoneInfo("America/Argentina/Buenos_Aires")


def dt(hora: int, minuto: int = 0, dia: int = 5) -> datetime:
    return datetime(2026, 10, dia, hora, minuto, tzinfo=BA)  # lunes 2026-10-05


DIA = Ventana(dt(0), dt(0, dia=6))

_INSERT_BOOKING = (
    "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, "
    "time_range, status) values ($1, $2, $3, $4, tstzrange($5, $6), 'confirmed')"
)


class _Revertir(Exception):
    """Fuerza el ROLLBACK de `tenant_tx` para probar una reserva sin dejarla."""


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
async def pool_bot(database_url, roles_password):
    pool = await crear_pool(
        _dsn(database_url, "bot_service", roles_password), min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def escenario(db_superuser):
    """Tenant A: p1 y p2 (lunes 9-12), p3 inactivo, p4 sin el servicio s30; servicios de 30 y 45. Tenant B aparte."""
    sufijo = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4(), uuid.uuid4()
    e = {
        "a": a,
        "b": b,
        "p1": uuid.uuid4(),
        "p2": uuid.uuid4(),
        "p3": uuid.uuid4(),
        "p4": uuid.uuid4(),
        "pb": uuid.uuid4(),
        "s30": uuid.uuid4(),
        "s45": uuid.uuid4(),
        "sb": uuid.uuid4(),
        "cliente": uuid.uuid4(),
    }
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        a,
        f"it-disp-a-{sufijo}",
        b,
        f"it-disp-b-{sufijo}",
    )
    profesionales = (("p1", a, True), ("p2", a, True), ("p3", a, False), ("p4", a, True), ("pb", b, True))
    for clave, tenant, activo in profesionales:
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name, is_active) values ($1, $2, $3, $4)",
            e[clave],
            tenant,
            clave,
            activo,
        )
    for clave, tenant, minutos in (("s30", a, 30), ("s45", a, 45), ("sb", b, 30)):
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, $3, $4, 10)",
            e[clave],
            tenant,
            clave,
            minutos,
        )
    ofrece = (
        ("p1", a, ("s30", "s45")),
        ("p2", a, ("s30", "s45")),
        ("p3", a, ("s30",)),
        ("p4", a, ("s45",)),
        ("pb", b, ("sb",)),
    )
    for prof, tenant, servicios in ofrece:
        for s in servicios:
            await db_superuser.execute(
                "insert into public.professional_services (tenant_id, professional_id, service_id) values ($1, $2, $3)",
                tenant,
                e[prof],
                e[s],
            )
    for prof, tenant, _ in profesionales:
        await db_superuser.execute(
            "insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time) "
            "values ($1, $2, 1, '09:00', '12:00')",
            tenant,
            e[prof],
        )
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id) values ($1, $2, 'telegram', 'u')",
        e["cliente"],
        a,
    )
    try:
        yield e
    finally:
        for tabla in (
            "bookings",
            "availability_blocks",
            "availability_rules",
            "professional_services",
            "professionals",
            "services",
            "end_customers",
        ):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", [a, b])
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", [a, b])


async def _booking(db, e, prof, desde, hasta, status="confirmed"):
    await db.execute(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, "
        "pending_expires_at) values ($1, $2, $3, $4, tstzrange($5, $6), $7, "
        "case when $7 = 'pending' then now() + interval '1 day' end)",
        e["a"],
        e["cliente"],
        e["s30"],
        e[prof],
        desde,
        hasta,
        status,
    )


async def _bloqueo(db, e, prof, desde, hasta):
    await db.execute(
        "insert into public.availability_blocks (tenant_id, professional_id, time_range) "
        "values ($1, $2, tstzrange($3, $4))",
        e["a"],
        e[prof] if prof else None,
        desde,
        hasta,
    )


async def _huecos(pool, e, prof, servicio, rango=DIA, tenant="a", paso=30):
    async with tenant_tx(pool, e[tenant]) as conn:
        return await calcular_huecos(conn, e[prof], e[servicio], rango, paso_minutos=paso)


def _horas(huecos):
    return [(h.inicio.astimezone(BA).hour, h.inicio.astimezone(BA).minute) for h in huecos]


async def test_sin_bookings_ni_bloqueos_devuelve_toda_la_ventana(pool_bot, escenario):
    huecos = await _huecos(pool_bot, escenario, "p1", "s30")
    assert _horas(huecos) == [(9, 0), (9, 30), (10, 0), (10, 30), (11, 0), (11, 30)]


async def test_usa_duration_minutes_del_servicio(pool_bot, escenario):
    huecos = await _huecos(pool_bot, escenario, "p1", "s45", paso=15)
    assert huecos
    assert all((h.fin - h.inicio).total_seconds() == 45 * 60 for h in huecos)
    assert huecos[-1].fin == dt(12)


async def test_booking_y_bloqueo_parcial_recortan(pool_bot, escenario, db_superuser):
    e = escenario
    await _booking(db_superuser, e, "p1", dt(10), dt(10, 30))
    await _bloqueo(db_superuser, e, "p1", dt(11), dt(11, 15))
    assert _horas(await _huecos(pool_bot, e, "p1", "s30")) == [(9, 0), (9, 30), (10, 30), (11, 30)]
    # el otro profesional no se ve afectado por lo de p1
    assert len(await _huecos(pool_bot, e, "p2", "s30")) == 6


async def test_bloqueo_de_todo_el_local_aplica_a_todos(pool_bot, escenario, db_superuser):
    e = escenario
    await _bloqueo(db_superuser, e, None, dt(9), dt(11))
    for prof in ("p1", "p2"):
        assert _horas(await _huecos(pool_bot, e, prof, "s30")) == [(11, 0), (11, 30)]


async def test_pending_bloquea_y_cancelled_libera(pool_bot, escenario, db_superuser):
    e = escenario
    await _booking(db_superuser, e, "p1", dt(9), dt(9, 30), status="pending")
    await _booking(db_superuser, e, "p1", dt(10), dt(10, 30), status="cancelled")
    horas = _horas(await _huecos(pool_bot, e, "p1", "s30"))
    assert (9, 0) not in horas
    assert (10, 0) in horas


async def test_profesional_inactivo_o_que_no_ofrece_el_servicio_no_tiene_huecos(pool_bot, escenario):
    e = escenario
    assert await _huecos(pool_bot, e, "p3", "s30") == []  # inactivo
    assert await _huecos(pool_bot, e, "p4", "s30") == []  # no ofrece s30


async def test_servicio_inexistente_no_tiene_huecos(pool_bot, escenario):
    e = dict(escenario, fantasma=uuid.uuid4())
    assert await _huecos(pool_bot, e, "p1", "fantasma") == []


async def test_otro_tenant_no_ve_datos_ajenos(pool_bot, escenario):
    e = escenario
    # con el contexto del tenant B, el profesional de A es invisible por RLS
    assert await _huecos(pool_bot, e, "p1", "s30", tenant="b") == []
    assert len(await _huecos(pool_bot, e, "pb", "sb", tenant="b")) == 6


async def test_un_hueco_calculado_siempre_es_reservable(pool_bot, escenario, db_superuser):
    e = escenario
    await _booking(db_superuser, e, "p1", dt(10), dt(10, 30))
    await _bloqueo(db_superuser, e, "p1", dt(11, 10), dt(11, 20))
    huecos = await _huecos(pool_bot, e, "p1", "s30", paso=15)
    assert huecos
    for h in huecos:
        with pytest.raises(_Revertir):
            async with tenant_tx(pool_bot, e["a"]) as conn:
                await conn.execute(_INSERT_BOOKING, e["a"], e["cliente"], e["s30"], e["p1"], h.inicio, h.fin)
                raise _Revertir


async def test_control_el_constraint_rechaza_lo_que_no_es_hueco(pool_bot, escenario, db_superuser):
    # Sanidad del test anterior: pisar el booking existente sí lo rechaza el exclusion constraint.
    e = escenario
    await _booking(db_superuser, e, "p1", dt(10), dt(10, 30))
    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        async with tenant_tx(pool_bot, e["a"]) as conn:
            await conn.execute(_INSERT_BOOKING, e["a"], e["cliente"], e["s30"], e["p1"], dt(10, 15), dt(10, 45))
