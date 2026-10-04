"""Aislamiento de las consultas compartidas con Postgres real (§2.11.4).

La misma función corre como `dashboard_service` (tenant_tx, RLS por tenant) y como
`platform_admin_readonly` (readonly_tx: la RLS le deja ver todos los tenants, así que solo el
`WHERE tenant_id = $1` explícito evita la fuga). En ambos casos debe devolver únicamente las filas
del tenant pedido, y lo mismo en los dos roles.
"""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from platform_tenancy import crear_pool, readonly_tx, tenant_tx

from turnos_queries import (
    detalle_booking,
    listar_bookings,
    listar_clientes,
    listar_packs,
    listar_profesionales,
    listar_servicios,
)

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

DESDE = datetime(2030, 1, 1, tzinfo=timezone.utc)  # noqa: UP017 (UTC no existe en 3.10)
HASTA = DESDE + timedelta(days=7)
ROLES = ("dashboard_service", "platform_admin_readonly")


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
async def escenario(db_superuser):
    """Dos tenants, cada uno con 2 bookings (confirmed y cancelled), 2 servicios, 1 cliente, 1 pack."""
    sufijo = uuid.uuid4().hex[:8]
    datos = {}
    for k in ("a", "b"):
        d = {
            "tenant": uuid.uuid4(),
            "prof": uuid.uuid4(),
            "servicio": uuid.uuid4(),
            "servicio_inactivo": uuid.uuid4(),
            "cliente": uuid.uuid4(),
            "pack": uuid.uuid4(),
            "booking": uuid.uuid4(),
            "booking_cancelado": uuid.uuid4(),
        }
        datos[k] = d
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            d["tenant"], k.upper(), f"it-tq-{k}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'S', 30, 10)",
            d["servicio"], d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price, is_active) "
            "values ($1, $2, 'Viejo', 30, 10, false)",
            d["servicio_inactivo"], d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id, name) "
            "values ($1, $2, 'telegram', 'u', 'Cliente')",
            d["cliente"], d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin) "
            "values ($1, $2, $3, $4, 5, 'manual_grant')",
            d["pack"], d["tenant"], d["cliente"], d["servicio"],
        )
        # Mismo horario, pero uno cancelado: el exclusion constraint ignora los cancelados.
        for bid, estado in ((d["booking"], "confirmed"), (d["booking_cancelado"], "cancelled")):
            inicio = DESDE + timedelta(days=1, hours=10)
            await db_superuser.execute(
                "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, "
                "time_range, status) values ($1, $2, $3, $4, $5, tstzrange($6, $7), $8)",
                bid, d["tenant"], d["cliente"], d["servicio"], d["prof"],
                inicio, inicio + timedelta(minutes=30), estado,
            )
    try:
        yield datos
    finally:
        ids = [d["tenant"] for d in datos.values()]
        for tabla in ("bookings", "booking_packs", "professionals", "services", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", ids)


@pytest.fixture
async def pools(database_url, roles_password):
    creados = {}
    for rol in ROLES:
        creados[rol] = await crear_pool(
            _dsn(database_url, rol, roles_password), min_size=1, max_size=2, statement_cache_size=0
        )
    try:
        yield creados
    finally:
        for p in creados.values():
            await p.close()


def _tx(rol, pools, tenant_contexto):
    """Transacción del rol: dashboard_service con su tenant; readonly sin tenant (ve todo por RLS)."""
    if rol == "dashboard_service":
        return tenant_tx(pools[rol], tenant_contexto)
    return readonly_tx(pools[rol])


async def _ejecutar_todas(conn, tenant_id, d):
    """Cada función con el tenant pedido; devuelve {nombre: lista de filas}."""
    detalle = await detalle_booking(conn, tenant_id=tenant_id, booking_id=d["booking"])
    return {
        "listar_bookings": await listar_bookings(conn, tenant_id=tenant_id, desde=DESDE, hasta=HASTA),
        "listar_bookings_por_profesional": await listar_bookings(
            conn, tenant_id=tenant_id, desde=DESDE, hasta=HASTA, professional_id=d["prof"]
        ),
        "listar_bookings_por_estado": await listar_bookings(
            conn, tenant_id=tenant_id, desde=DESDE, hasta=HASTA, estados=["confirmed", "cancelled"]
        ),
        "detalle_booking": [detalle] if detalle else [],
        "listar_clientes": await listar_clientes(conn, tenant_id=tenant_id),
        "listar_servicios": await listar_servicios(conn, tenant_id=tenant_id, incluir_inactivos=True),
        "listar_profesionales": await listar_profesionales(conn, tenant_id=tenant_id),
        "listar_packs": await listar_packs(conn, tenant_id=tenant_id),
    }


@pytest.mark.parametrize("rol", ROLES)
async def test_cada_funcion_devuelve_solo_filas_del_tenant_pedido(rol, pools, escenario):
    a, b = escenario["a"], escenario["b"]
    for pedido, otro in ((a, b), (b, a)):
        async with _tx(rol, pools, pedido["tenant"]) as conn:
            resultados = await _ejecutar_todas(conn, pedido["tenant"], pedido)
        for nombre, filas in resultados.items():
            assert filas, f"{nombre} devolvió vacío como {rol}"
            assert {f["tenant_id"] for f in filas} == {pedido["tenant"]}, f"{nombre} filtró datos como {rol}"
            assert otro["tenant"] not in {f["tenant_id"] for f in filas}


async def test_readonly_y_dashboard_devuelven_lo_mismo(pools, escenario):
    a = escenario["a"]
    async with _tx("dashboard_service", pools, a["tenant"]) as conn:
        como_dashboard = await _ejecutar_todas(conn, a["tenant"], a)
    async with _tx("platform_admin_readonly", pools, None) as conn:
        como_readonly = await _ejecutar_todas(conn, a["tenant"], a)
    assert como_dashboard == como_readonly


async def test_readonly_ve_ambos_tenants_en_crudo_pero_la_funcion_solo_uno(pools, escenario):
    """Valida el escenario: sin el filtro explícito, readonly vería los dos tenants."""
    a, b = escenario["a"], escenario["b"]
    async with readonly_tx(pools["platform_admin_readonly"]) as conn:
        crudo = {r["tenant_id"] for r in await conn.fetch("select tenant_id from public.bookings")}
        filtrado = await listar_bookings(conn, tenant_id=a["tenant"], desde=DESDE, hasta=HASTA)
    assert {a["tenant"], b["tenant"]} <= crudo
    assert {f["tenant_id"] for f in filtrado} == {a["tenant"]}


@pytest.mark.parametrize("rol", ROLES)
async def test_detalle_de_booking_ajeno_no_se_devuelve(rol, pools, escenario):
    a, b = escenario["a"], escenario["b"]
    async with _tx(rol, pools, a["tenant"]) as conn:
        assert await detalle_booking(conn, tenant_id=a["tenant"], booking_id=b["booking"]) is None


async def test_filtros_de_estado_rango_y_servicios_inactivos(pools, escenario):
    a = escenario["a"]
    async with _tx("dashboard_service", pools, a["tenant"]) as conn:
        confirmados = await listar_bookings(
            conn, tenant_id=a["tenant"], desde=DESDE, hasta=HASTA, estados=["confirmed"]
        )
        todos = await listar_bookings(conn, tenant_id=a["tenant"], desde=DESDE, hasta=HASTA)
        fuera_de_rango = await listar_bookings(
            conn, tenant_id=a["tenant"], desde=HASTA, hasta=HASTA + timedelta(days=1)
        )
        activos = await listar_servicios(conn, tenant_id=a["tenant"])
        todos_servicios = await listar_servicios(conn, tenant_id=a["tenant"], incluir_inactivos=True)
    assert [x["id"] for x in confirmados] == [a["booking"]]
    assert {x["id"] for x in todos} == {a["booking"], a["booking_cancelado"]}
    assert fuera_de_rango == []
    assert [x["id"] for x in activos] == [a["servicio"]]
    assert len(todos_servicios) == 2


async def test_detalle_trae_nombres_de_cliente_servicio_y_profesional(pools, escenario):
    a = escenario["a"]
    async with _tx("dashboard_service", pools, a["tenant"]) as conn:
        det = await detalle_booking(conn, tenant_id=a["tenant"], booking_id=a["booking"])
    assert det["id"] == a["booking"]
    assert (det["customer_name"], det["service_name"], det["professional_name"]) == ("Cliente", "S", "P")
