"""Encolado de avisos contra Postgres real, con el rol `dashboard_service` y el contexto de tenant de la transacción.

Cubre lo que la conexión simulada no puede: que la base acepta cada plantilla y canal, la forma de la fila
(destino negocio sin canal ni ficha), el rollback y el aislamiento entre dos tenants.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from turnos_negocio import plantillas_cliente, plantillas_negocio

from turnos_dominio import encolar_aviso_cliente, encolar_aviso_negocio

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
INICIO = datetime(2030, 3, 4, 13, 0, tzinfo=UTC)


@pytest.fixture
async def dos_tenants(db_superuser):
    """Dos tenants, cada uno con una ficha de cliente vigente."""
    sufijo = uuid.uuid4().hex[:8]
    datos = {}
    for letra in ("a", "b"):
        tenant, cliente = uuid.uuid4(), uuid.uuid4()
        datos[letra] = (tenant, cliente)
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            tenant,
            letra.upper(),
            f"it-avisos-{letra}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
            "values ($1, $2, 'telegram', $3)",
            cliente,
            tenant,
            f"chat-{uuid.uuid4().hex[:8]}",
        )
    ids = [datos["a"][0], datos["b"][0]]
    try:
        yield datos
    finally:
        await db_superuser.execute("delete from public.notification_outbox where tenant_id = any($1::uuid[])", ids)
        await db_superuser.execute("delete from public.end_customers where tenant_id = any($1::uuid[])", ids)
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", ids)


async def _contexto(conn, tenant):
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))


async def _filas(db, tenant):
    return await db.fetch(
        "select destino, channel, end_customer_id, template, params from public.notification_outbox "
        "where tenant_id = $1 order by template",
        tenant,
    )


async def test_un_rollback_no_deja_ningun_aviso(conectar_como, db_superuser, dos_tenants):
    tenant, cliente = dos_tenants["a"]
    dashboard = await conectar_como("dashboard_service")
    tx = dashboard.transaction()
    await tx.start()
    await _contexto(dashboard, tenant)
    await encolar_aviso_cliente(
        dashboard,
        end_customer_id=cliente,
        canal="telegram",
        plantilla="turno_confirmado",
        booking_id=uuid.uuid4(),
        inicio=INICIO,
    )
    await encolar_aviso_negocio(dashboard, plantilla="aviso_negocio_reserva", booking_id=uuid.uuid4(), inicio=INICIO)
    await tx.rollback()
    assert await _filas(db_superuser, tenant) == []


async def test_el_aviso_al_negocio_queda_sin_canal_ni_ficha_y_el_del_cliente_con_ambos(
    conectar_como, db_superuser, dos_tenants
):
    tenant, cliente = dos_tenants["a"]
    reserva = uuid.uuid4()
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        await encolar_aviso_negocio(dashboard, plantilla="aviso_negocio_reserva", booking_id=reserva, inicio=INICIO)
        await encolar_aviso_cliente(
            dashboard,
            end_customer_id=cliente,
            canal="email",
            plantilla="cambio_horario_aprobado",
            booking_id=reserva,
            inicio=INICIO,
        )
    negocio, del_cliente = await _filas(db_superuser, tenant)  # orden por plantilla: aviso_negocio_*, cambio_horario_*
    assert (negocio["destino"], negocio["channel"], negocio["end_customer_id"]) == ("negocio", None, None)
    assert negocio["template"] == "aviso_negocio_reserva"
    assert (del_cliente["destino"], del_cliente["channel"], del_cliente["end_customer_id"]) == (
        "cliente",
        "email",
        cliente,
    )
    for fila in (negocio, del_cliente):
        assert json.loads(fila["params"]) == {"booking_id": str(reserva), "inicio": INICIO.isoformat()}


@pytest.mark.parametrize("plantilla", sorted(plantillas_negocio()))
async def test_la_base_acepta_cada_plantilla_de_negocio(conectar_como, db_superuser, dos_tenants, plantilla):
    tenant, _ = dos_tenants["a"]
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        await encolar_aviso_negocio(dashboard, plantilla=plantilla, booking_id=uuid.uuid4(), inicio=INICIO)
    assert [f["template"] for f in await _filas(db_superuser, tenant)] == [plantilla]


@pytest.mark.parametrize("plantilla", sorted(plantillas_cliente()))
async def test_la_base_acepta_cada_plantilla_de_cliente(conectar_como, db_superuser, dos_tenants, plantilla):
    tenant, cliente = dos_tenants["a"]
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        await encolar_aviso_cliente(
            dashboard,
            end_customer_id=cliente,
            canal="telegram",
            plantilla=plantilla,
            booking_id=uuid.uuid4(),
            inicio=INICIO,
        )
    assert [f["template"] for f in await _filas(db_superuser, tenant)] == [plantilla]


async def test_los_avisos_de_un_tenant_no_aparecen_en_el_otro(conectar_como, db_superuser, dos_tenants):
    (tenant_a, cliente_a), (tenant_b, _) = dos_tenants["a"], dos_tenants["b"]
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant_a)
        await encolar_aviso_negocio(
            dashboard, plantilla="aviso_negocio_reserva", booking_id=uuid.uuid4(), inicio=INICIO
        )
        await encolar_aviso_cliente(
            dashboard,
            end_customer_id=cliente_a,
            canal="telegram",
            plantilla="turno_confirmado",
            booking_id=uuid.uuid4(),
            inicio=INICIO,
        )
    assert len(await _filas(db_superuser, tenant_a)) == 2
    assert await _filas(db_superuser, tenant_b) == []
    # Con el contexto del otro tenant, el rol de la aplicación no ve nada de A (RLS).
    async with dashboard.transaction():
        await _contexto(dashboard, tenant_b)
        assert await dashboard.fetchval("select count(*) from public.notification_outbox") == 0


async def test_el_aviso_con_la_ficha_de_otro_tenant_se_rechaza_y_no_deja_fila(conectar_como, db_superuser, dos_tenants):
    (tenant_a, _), (tenant_b, cliente_b) = dos_tenants["a"], dos_tenants["b"]
    dashboard = await conectar_como("dashboard_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with dashboard.transaction():
            await _contexto(dashboard, tenant_a)
            await encolar_aviso_cliente(
                dashboard,
                end_customer_id=cliente_b,
                canal="telegram",
                plantilla="turno_confirmado",
                booking_id=uuid.uuid4(),
                inicio=INICIO,
            )
    assert await _filas(db_superuser, tenant_a) == [] and await _filas(db_superuser, tenant_b) == []


async def test_el_aviso_al_cliente_anonimizado_no_deja_fila_y_devuelve_none(conectar_como, db_superuser, dos_tenants):
    tenant, cliente = dos_tenants["a"]
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        await dashboard.execute("select set_config('app.current_actor_id', $1, true)", str(uuid.uuid4()))
        await dashboard.execute("select public.anonymize_end_customer($1)", cliente)
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        resultado = await encolar_aviso_cliente(
            dashboard,
            end_customer_id=cliente,
            canal="telegram",
            plantilla="turno_confirmado",
            booking_id=uuid.uuid4(),
            inicio=INICIO,
        )
    assert resultado is None and await _filas(db_superuser, tenant) == []
