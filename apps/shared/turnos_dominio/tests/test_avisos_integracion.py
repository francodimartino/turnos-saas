"""Encolado de avisos contra Postgres real, con el rol `dashboard_service` y el contexto de tenant de la transacción.

Cubre lo que la conexión simulada no puede: que la base acepta cada plantilla y canal, la forma de la fila
(destino negocio sin canal ni ficha), el rollback y el aislamiento entre dos tenants.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from turnos_negocio import plantillas_cliente, plantillas_negocio

from turnos_dominio import (
    encolar_aviso_cliente,
    encolar_aviso_negocio,
    encolar_aviso_segun_canales,
    leer_ficha_para_aviso,
)

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
    # Con el contexto del otro tenant el aviso es de B: A conserva solo los suyos.
    async with dashboard.transaction():
        await _contexto(dashboard, tenant_b)
        await encolar_aviso_negocio(
            dashboard, plantilla="aviso_negocio_cancelacion", booking_id=uuid.uuid4(), inicio=INICIO
        )
    assert [f["template"] for f in await _filas(db_superuser, tenant_b)] == ["aviso_negocio_cancelacion"]
    assert len(await _filas(db_superuser, tenant_a)) == 2


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
    await db_superuser.execute(
        "update public.end_customers set deleted_at = now(), channel = null, channel_user_id = null where id = $1",
        cliente,
    )
    dashboard = await conectar_como("dashboard_service")
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


# --- según los canales verificados de la ficha ------------------------------------------------------------------------


async def _ficha(db, tenant, canales):
    """Una ficha con esos canales de `canales_cliente` `(canal, identificador, verificado)`; sin canal heredado."""
    cliente = uuid.uuid4()
    await db.execute("insert into public.end_customers (id, tenant_id) values ($1, $2)", cliente, tenant)
    for canal, identificador, verificado in canales:
        await db.execute(
            "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
            "values ($1, $2, $3, $4, case when $5 then now() end)",
            tenant,
            cliente,
            canal,
            identificador,
            verificado,
        )
    return cliente


CASOS_DE_CANALES = [
    # (canales de la ficha, envío inmediato, canal de origen, demora esperada en segundos)
    ([("telegram", "tg-1", True)], True, "telegram", 300),
    ([("telegram", "tg-1", True), ("email", "a@ejemplo.ar", True)], False, "telegram", 0),
    ([("telegram", "tg-1", True), ("telegram", "tg-2", True)], False, "telegram", 0),
    ([("email", "a@ejemplo.ar", True)], False, "email", 0),
    ([("whatsapp", "549", True)], False, "whatsapp", 0),
    ([("whatsapp", "549", True), ("email", "a@ejemplo.ar", True)], False, "whatsapp", 0),
    # un canal sin verificar no cuenta: con un Telegram verificado y un email sin verificar es único Telegram
    ([("telegram", "tg-1", True), ("email", "a@ejemplo.ar", False)], True, "telegram", 300),
]


@pytest.mark.parametrize(("canales", "inmediato", "origen", "demora"), CASOS_DE_CANALES)
async def test_el_aviso_segun_los_canales_de_la_ficha_en_la_base_real(
    conectar_como, db_superuser, dos_tenants, canales, inmediato, origen, demora
):
    tenant, _ = dos_tenants["a"]
    cliente = await _ficha(db_superuser, tenant, canales)
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        aviso = await encolar_aviso_segun_canales(
            dashboard, end_customer_id=cliente, plantilla="turno_confirmado", booking_id=uuid.uuid4(), inicio=INICIO
        )
    assert (aviso.estado, aviso.envio_inmediato) == ("encolado", inmediato)
    if inmediato:
        assert aviso.destinatario_telegram == "tg-1"
    fila = await db_superuser.fetchrow(
        "select channel, status, extract(epoch from next_attempt_at - created_at) as espera "
        "from public.notification_outbox where id = $1",
        aviso.outbox_id,
    )
    assert (fila["channel"], fila["status"]) == (origen, "pending")
    assert round(float(fila["espera"])) == demora


async def test_ficha_sin_canal_verificado_no_encola_en_la_base_real(conectar_como, db_superuser, dos_tenants):
    tenant, _ = dos_tenants["a"]
    cliente = await _ficha(db_superuser, tenant, [("email", "a@ejemplo.ar", False)])
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        aviso = await encolar_aviso_segun_canales(
            dashboard, end_customer_id=cliente, plantilla="turno_confirmado", booking_id=uuid.uuid4(), inicio=INICIO
        )
    assert (aviso.estado, aviso.outbox_id) == ("sin_canal", None)
    assert await _filas(db_superuser, tenant) == []


async def test_ficha_anonimizada_no_encola_en_la_base_real(conectar_como, db_superuser, dos_tenants):
    tenant, _ = dos_tenants["a"]
    cliente = await _ficha(db_superuser, tenant, [("email", "a@ejemplo.ar", True)])
    await db_superuser.execute("update public.end_customers set deleted_at = now() where id = $1", cliente)
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        aviso = await encolar_aviso_segun_canales(
            dashboard, end_customer_id=cliente, plantilla="turno_confirmado", booking_id=uuid.uuid4(), inicio=INICIO
        )
    assert aviso.estado == "cliente_anonimizado" and await _filas(db_superuser, tenant) == []


@pytest.mark.parametrize("rol", ["dashboard_service", "bot_service"])
async def test_la_ficha_de_otro_tenant_no_se_lee_ni_se_avisa(conectar_como, db_superuser, dos_tenants, rol):
    (tenant_a, _), (tenant_b, _) = dos_tenants["a"], dos_tenants["b"]
    cliente_b = await _ficha(db_superuser, tenant_b, [("telegram", "tg-b", True)])
    conn = await conectar_como(rol)
    async with conn.transaction():
        await _contexto(conn, tenant_a)
        assert await leer_ficha_para_aviso(conn, cliente_b) is None
        aviso = await encolar_aviso_segun_canales(
            conn, end_customer_id=cliente_b, plantilla="turno_confirmado", booking_id=uuid.uuid4(), inicio=INICIO
        )
    assert (aviso.estado, aviso.outbox_id) == ("sin_canal", None)
    assert await _filas(db_superuser, tenant_a) == [] and await _filas(db_superuser, tenant_b) == []


@pytest.mark.parametrize(("canales", "inmediato", "origen", "demora"), CASOS_DE_CANALES)
async def test_la_condicion_de_envio_inmediato_coincide_con_la_del_reclamo_del_job(
    conectar_como, db_superuser, dos_tenants, canales, inmediato, origen, demora
):
    """`solo_telegram` y `reclamar_avisos_telegram` deciden igual: la fila encolada solo la reclama el job si aplica."""
    tenant, _ = dos_tenants["a"]
    cliente = await _ficha(db_superuser, tenant, canales)
    dashboard = await conectar_como("dashboard_service")
    async with dashboard.transaction():
        await _contexto(dashboard, tenant)
        ficha = await leer_ficha_para_aviso(dashboard, cliente)
        outbox_id = await encolar_aviso_cliente(
            dashboard,
            end_customer_id=cliente,
            canal=origen,
            plantilla="turno_confirmado",
            booking_id=uuid.uuid4(),
            inicio=INICIO,
            primer_intento_en_segundos=0,
        )
    assert ficha is not None and ficha.solo_telegram is inmediato
    job = await conectar_como("scheduler_job")
    reclamadas = await job.fetch("select id from public.reclamar_avisos_telegram(500, 60, 5)")
    assert (outbox_id in [f["id"] for f in reclamadas]) is inmediato
