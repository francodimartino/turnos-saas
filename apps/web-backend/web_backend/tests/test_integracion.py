"""web-backend contra Postgres real con el rol `web_reservas`: slug, RLS entre negocios y mínimo privilegio."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

from platform_tenancy import tenant_tx  # noqa: E402

from web_backend.tests.soporte import AlmacenamientoFalso, app_de_prueba, http  # noqa: E402

UTC = timezone.utc  # noqa: UP017 - datetime.UTC no existe en Python 3.10
LUNES = "2026-11-09"  # lunes 9 de noviembre de 2026, una semana después del reloj de prueba
TABLAS_DEL_TENANT = (
    "audit_log",
    "bookings",
    "canales_cliente",
    "end_customers",
    "availability_blocks",
    "availability_rules",
    "professional_services",
    "professionals",
    "services",
)


class _PoolDeUnaConexion:
    """Adapta la conexión de `conectar_como("web_reservas")` a la interfaz de pool (acquire).

    El servicio nunca pide dos conexiones a la vez (resuelve el slug, suelta la conexión y recién después abre la
    transacción del negocio), así que una sola conexión alcanza.
    """

    def __init__(self, conn):
        self._conn = conn
        self.adquisiciones = 0

    @asynccontextmanager
    async def acquire(self):
        self.adquisiciones += 1
        yield self._conn


async def _borrar(conn, sql, *args):
    """DELETE de limpieza sin triggers: las invariantes de los registros históricos lo frenarían."""
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


async def _negocio(db, *, nombre, slug, status="active", settings=None):
    tenant = uuid.uuid4()
    await db.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at, settings) "
        "values ($1, $2, $3, $4, case when $4 = 'inactive' then now() end, $5::jsonb)",
        tenant,
        nombre,
        slug,
        status,
        json.dumps(settings or {}),
    )
    return tenant


async def _catalogo(db, tenant, *, servicio, profesional, precio="1500.50", duracion=30):
    """Un servicio y un profesional activos que se atienden entre sí en modo `auto`, con agenda los lunes 9 a 12."""
    s, p = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, $3, $4, $5)",
        s,
        tenant,
        servicio,
        duracion,
        Decimal(precio),
    )
    await db.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, $3)", p, tenant, profesional
    )
    await db.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
        "values ($1, $2, $3, 'auto')",
        tenant,
        p,
        s,
    )
    await db.execute(
        "insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time) "
        "values ($1, $2, 1, '09:00', '12:00')",
        tenant,
        p,
    )
    return s, p


def _hora(dia: str, hhmm: str) -> datetime:
    """Instante de una hora local de Buenos Aires (UTC-3, sin horario de verano)."""
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(dia).replace(hour=h, minute=m, tzinfo=UTC) + timedelta(hours=3)


@pytest.fixture
async def escenario(db_superuser):
    """Dos negocios activos con catálogo propio, uno suspendido, y slugs anteriores (uno vigente y uno vencido)."""
    sufijo = uuid.uuid4().hex[:8]
    slug_a, slug_b = f"it-web-a-{sufijo}", f"it-web-b-{sufijo}"
    logo = f"{{tenant}}/logo-{'a' * 32}.png"
    a = await _negocio(db_superuser, nombre="Estética Sol", slug=slug_a, settings={})
    logo = logo.format(tenant=a)
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1",
        a,
        json.dumps(
            {
                "local_direccion": "Av. Siempreviva 742",
                "color_principal": "#112233",
                "color_secundario": "#AABBCC",
                "logo_ruta": logo,
                # Claves que NO son públicas: no pueden salir por la API.
                "local_contacto": "contacto-interno-secreto",
                "booking_max_per_hour": 7,
                "local_horarios": "horarios-internos",
            }
        ),
    )
    b = await _negocio(db_superuser, nombre="Barbería Luna", slug=slug_b)
    suspendido = await _negocio(
        db_superuser, nombre="Negocio Suspendido", slug=f"it-web-s-{sufijo}", status="suspended"
    )
    s_a, p_a = await _catalogo(db_superuser, a, servicio="Corte", profesional="Ana")
    s_b, p_b = await _catalogo(db_superuser, b, servicio="Barba", profesional="Beto", precio="900", duracion=20)
    # Lo que no se ofrece al público: servicio inactivo, profesional inactivo y un servicio solo con seña.
    inactivo = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price, is_active) "
        "values ($1, $2, 'Servicio inactivo', 30, 100, false)",
        inactivo,
        a,
    )
    await db_superuser.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id) values ($1, $2, $3)",
        a,
        p_a,
        inactivo,
    )
    p_inactivo = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name, is_active) "
        "values ($1, $2, 'Profesional inactivo', false)",
        p_inactivo,
        a,
    )
    await db_superuser.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id) values ($1, $2, $3)",
        a,
        p_inactivo,
        s_a,
    )
    solo_sena = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price, deposit_amount) "
        "values ($1, $2, 'Servicio con seña', 30, 100, 50)",
        solo_sena,
        a,
    )
    await db_superuser.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
        "values ($1, $2, $3, 'deposit')",
        a,
        p_a,
        solo_sena,
    )
    # Un turno de 10:00 a 10:30 del lunes y un bloqueo de todo el local de 11:00 a 11:15.
    cliente = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id) "
        "values ($1, 'telegram', $2) returning id",
        a,
        uuid.uuid4().hex,
    )
    await db_superuser.execute(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
        "values ($1, $2, $3, $4, tstzrange($5, $6), 'confirmed')",
        a,
        cliente,
        s_a,
        p_a,
        _hora(LUNES, "10:00"),
        _hora(LUNES, "10:30"),
    )
    await db_superuser.execute(
        "insert into public.availability_blocks (tenant_id, professional_id, time_range) "
        "values ($1, null, tstzrange($2, $3))",
        a,
        _hora(LUNES, "11:00"),
        _hora(LUNES, "11:15"),
    )
    viejo, vencido = f"it-web-viejo-{sufijo}", f"it-web-vencido-{sufijo}"
    await db_superuser.execute(
        "insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta) "
        "values ($1, $2, now() - interval '1 day', now() + interval '179 days')",
        viejo,
        a,
    )
    await db_superuser.execute(
        "insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta) "
        "values ($1, $2, now() - interval '200 days', now() - interval '20 days')",
        vencido,
        a,
    )
    try:
        yield {
            "a": a,
            "b": b,
            "slug_a": slug_a,
            "slug_b": slug_b,
            "slug_suspendido": f"it-web-s-{sufijo}",
            "slug_viejo": viejo,
            "slug_vencido": vencido,
            "s_a": s_a,
            "p_a": p_a,
            "s_b": s_b,
            "p_b": p_b,
            "inactivo": inactivo,
            "solo_sena": solo_sena,
            "p_inactivo": p_inactivo,
            "logo": logo,
        }
    finally:
        tenants = [a, b, suspendido]
        await _borrar(db_superuser, "delete from public.slugs_anteriores where tenant_id = any($1::uuid[])", tenants)
        for tabla in TABLAS_DEL_TENANT:
            await _borrar(db_superuser, f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", tenants)


@pytest.fixture
async def app(conectar_como):
    conn = await conectar_como("web_reservas")
    app, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn), almacenamiento=AlmacenamientoFalso())
    return app


def _params(e, **cambios):
    p = {"servicio_id": str(e["s_a"]), "profesional_id": str(e["p_a"]), "desde": LUNES, "hasta": LUNES}
    p.update(cambios)
    return p


# --- Página del negocio ------------------------------------------------------------------------------------------


async def test_la_pagina_de_un_negocio_trae_solo_lo_publico_y_solo_lo_reservable(app, escenario):
    r = await http(app).get(f"/negocios/{escenario['slug_a']}")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["nombre"] == "Estética Sol" and cuerpo["slug"] == escenario["slug_a"]
    assert cuerpo["direccion"] == "Av. Siempreviva 742"
    assert (cuerpo["color_principal"], cuerpo["color_secundario"]) == ("#112233", "#AABBCC")
    assert cuerpo["logo"].endswith(f"{escenario['logo']}?token=firmado")
    assert cuerpo["servicios"] == [
        {
            "id": str(escenario["s_a"]),
            "nombre": "Corte",
            "duracion_minutos": 30,
            "precio": 1500.5,
            "profesionales": [str(escenario["p_a"])],
        }
    ]
    assert cuerpo["profesionales"] == [
        {"id": str(escenario["p_a"]), "nombre": "Ana", "servicios": [str(escenario["s_a"])]}
    ]
    for oculto in ("contacto-interno-secreto", "horarios-internos", "booking_max_per_hour", "local_contacto"):
        assert oculto not in r.text
    for ajeno in (escenario["s_b"], escenario["p_b"], escenario["b"], "Beto", "Barba", "Servicio inactivo", "seña"):
        assert str(ajeno) not in r.text


async def test_cada_negocio_ve_solo_lo_suyo(app, escenario):
    a = (await http(app).get(f"/negocios/{escenario['slug_a']}")).json()
    b = (await http(app).get(f"/negocios/{escenario['slug_b']}")).json()
    assert [s["nombre"] for s in a["servicios"]] == ["Corte"] and [s["nombre"] for s in b["servicios"]] == ["Barba"]
    assert b["nombre"] == "Barbería Luna" and b["direccion"] is None and b["logo"] is None
    assert b["color_principal"] is None and b["color_secundario"] is None
    ids_a = {s["id"] for s in a["servicios"]} | {p["id"] for p in a["profesionales"]}
    ids_b = {s["id"] for s in b["servicios"]} | {p["id"] for p in b["profesionales"]}
    assert ids_a.isdisjoint(ids_b)


async def test_un_negocio_suspendido_inexistente_o_reservado_dan_el_mismo_404(app, escenario):
    cliente = http(app)
    respuestas = [
        await cliente.get(f"/negocios/{escenario['slug_suspendido']}"),
        await cliente.get("/negocios/no-existe-nunca"),
        await cliente.get("/negocios/admin"),
        await cliente.get("/negocios/reservas-api"),
    ]
    for r in respuestas:
        assert r.status_code == 404 and r.json()["detail"]["codigo"] == "no_disponible"
    assert len({r.text for r in respuestas}) == 1


async def test_un_negocio_que_se_suspende_deja_de_mostrarse(app, escenario, db_superuser):
    assert (await http(app).get(f"/negocios/{escenario['slug_b']}")).status_code == 200
    await db_superuser.execute("update public.tenants set status = 'suspended' where id = $1", escenario["b"])
    assert (await http(app).get(f"/negocios/{escenario['slug_b']}")).status_code == 404
    await db_superuser.execute("update public.tenants set status = 'active' where id = $1", escenario["b"])
    assert (await http(app).get(f"/negocios/{escenario['slug_b']}")).status_code == 200


async def test_un_slug_anterior_vigente_redirige_y_uno_vencido_no_existe(app, escenario):
    cliente = http(app)
    r = await cliente.get(f"/negocios/{escenario['slug_viejo']}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"/negocios/{escenario['slug_a']}"
    assert r.json()["detail"]["slug_vigente"] == escenario["slug_a"]
    seguido = await cliente.get(f"/negocios/{escenario['slug_viejo']}", follow_redirects=True)
    assert seguido.status_code == 200 and seguido.json()["slug"] == escenario["slug_a"]
    vencido = await cliente.get(f"/negocios/{escenario['slug_vencido']}")
    assert vencido.status_code == 404 and vencido.json()["detail"]["codigo"] == "no_disponible"


# --- Horarios libres -----------------------------------------------------------------------------------------------


async def test_los_huecos_descuentan_turnos_y_bloqueos_con_el_calculo_de_turnos_dominio(app, escenario):
    r = await http(app).get(f"/negocios/{escenario['slug_a']}/huecos", params=_params(escenario))
    assert r.status_code == 200, r.text
    inicios = [h["inicio"][11:16] for h in r.json()["huecos"]]
    # Agenda 9 a 12; turno 10:00-10:30 y bloqueo 11:00-11:15; servicio de 30 minutos, cada 15.
    assert inicios == ["09:00", "09:15", "09:30", "10:30", "11:15", "11:30"]
    assert {h["inicio"][:10] for h in r.json()["huecos"]} == {LUNES}
    assert r.json()["huecos"][0] == {"inicio": f"{LUNES}T09:00:00-03:00", "fin": f"{LUNES}T09:30:00-03:00"}


async def test_un_servicio_o_profesional_de_otro_negocio_no_existe_para_este(app, escenario):
    cliente = http(app)
    for cambios in (
        {"servicio_id": str(escenario["s_b"])},
        {"profesional_id": str(escenario["p_b"])},
        {"servicio_id": str(escenario["s_b"]), "profesional_id": str(escenario["p_b"])},
    ):
        r = await cliente.get(f"/negocios/{escenario['slug_a']}/huecos", params=_params(escenario, **cambios))
        assert r.status_code == 404 and r.json()["detail"]["codigo"] == "no_encontrado"
    # Y al revés: los ids de A no sirven bajo el slug de B.
    r = await cliente.get(f"/negocios/{escenario['slug_b']}/huecos", params=_params(escenario))
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "no_encontrado"


@pytest.mark.parametrize("clave", ["inactivo", "solo_sena"])
async def test_un_servicio_inactivo_o_solo_con_sena_no_se_ofrece(app, escenario, clave):
    r = await http(app).get(
        f"/negocios/{escenario['slug_a']}/huecos", params=_params(escenario, servicio_id=str(escenario[clave]))
    )
    assert r.status_code == 404


async def test_un_profesional_inactivo_no_se_ofrece(app, escenario):
    r = await http(app).get(
        f"/negocios/{escenario['slug_a']}/huecos",
        params=_params(escenario, profesional_id=str(escenario["p_inactivo"])),
    )
    assert r.status_code == 404


async def test_un_dia_sin_agenda_no_tiene_huecos(app, escenario):
    r = await http(app).get(
        f"/negocios/{escenario['slug_a']}/huecos", params=_params(escenario, desde="2026-11-10", hasta="2026-11-10")
    )
    assert r.status_code == 200 and r.json()["huecos"] == []


async def test_la_anticipacion_minima_del_negocio_se_respeta(conectar_como, escenario, db_superuser):
    conn = await conectar_como("web_reservas")
    ahora = datetime(2026, 11, 9, 11, 0, tzinfo=UTC)  # lunes 08:00 en Buenos Aires
    app, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn), reloj=lambda: ahora)
    url = f"/negocios/{escenario['slug_a']}/huecos"

    por_defecto = await http(app).get(url, params=_params(escenario))  # 30 minutos: desde las 08:30
    assert [h["inicio"][11:16] for h in por_defecto.json()["huecos"]] == [
        "09:00",
        "09:15",
        "09:30",
        "10:30",
        "11:15",
        "11:30",
    ]
    await db_superuser.execute(
        "update public.tenants set settings = settings || '{\"booking_min_notice_minutes\": 180}'::jsonb where id = $1",
        escenario["a"],
    )
    con_aviso = await http(app).get(url, params=_params(escenario))  # 180 minutos: desde las 11:00
    assert [h["inicio"][11:16] for h in con_aviso.json()["huecos"]] == ["11:15", "11:30"]


# --- Mínimo privilegio y aislamiento con el rol real ---------------------------------------------------------------


async def test_un_rol_sin_permiso_sobre_la_funcion_publica_recibe_503_y_nada_filtrado(conectar_como, escenario):
    conn = await conectar_como("bot_service")  # no tiene EXECUTE sobre resolver_slug_tenant
    app, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    r = await http(app).get(f"/negocios/{escenario['slug_a']}")
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "base_no_disponible"
    assert "permission" not in r.text and "resolver_slug_tenant" not in r.text and "Sol" not in r.text


async def test_el_rol_de_la_web_solo_lee_id_nombre_y_settings_de_tenants(conectar_como, escenario):
    import asyncpg

    pool = _PoolDeUnaConexion(await conectar_como("web_reservas"))
    async with tenant_tx(pool, escenario["a"]) as c:
        filas = await c.fetch("select id, name, settings from public.tenants")
    assert [f["id"] for f in filas] == [escenario["a"]]  # la RLS deja ver solo el negocio del contexto
    for columna in ("plan", "status", "paid_until", "billing_notes", "slug"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with tenant_tx(pool, escenario["a"]) as c:
                await c.fetch(f"select {columna} from public.tenants")


async def test_sin_contexto_de_tenant_el_rol_de_la_web_no_ve_ningun_negocio_ni_catalogo(conectar_como, escenario):
    conn = await conectar_como("web_reservas")
    async with conn.transaction():
        assert await conn.fetch("select id from public.tenants") == []
        assert await conn.fetch("select id from public.services") == []
        assert await conn.fetch("select id from public.professionals") == []


async def test_el_rol_de_la_web_no_ve_las_tablas_internas(conectar_como, escenario):
    import asyncpg

    pool = _PoolDeUnaConexion(await conectar_como("web_reservas"))
    for tabla in ("tenant_integrations", "audit_log", "tenant_users", "notification_outbox"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with tenant_tx(pool, escenario["a"]) as c:
                await c.fetch(f"select * from public.{tabla}")


async def test_el_contexto_de_tenant_de_un_pedido_no_se_filtra_al_siguiente(app, escenario):
    cliente = http(app)
    primero = await cliente.get(f"/negocios/{escenario['slug_a']}")
    segundo = await cliente.get(f"/negocios/{escenario['slug_b']}")
    assert primero.json()["nombre"] == "Estética Sol" and segundo.json()["nombre"] == "Barbería Luna"
