"""/clientes contra Postgres real con el rol `dashboard_service`: RLS, permisos, anonimización y calendario.

Datos ficticios (teléfonos del rango de ejemplo, nombres inventados). Dos tenants para probar el aislamiento.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from platform_calendar import EventoNoLimpiado  # noqa: E402

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_clientes import app_clientes, fabrica_de  # noqa: E402

TEL_A = "+54 9 11 5550-0101"
TEL_A_E164 = "+5491155500101"
VARIABLES_GOOGLE = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class CalendarioReal:
    """Lo que usa el endpoint de `ServicioCalendario`: registra lo recibido y devuelve los fallos configurados."""

    def __init__(self, fallidos=()):
        self.fallidos = list(fallidos)
        self.pares = None

    async def anonimizar_eventos(self, pares, texto):
        self.pares = list(pares)
        return self.fallidos


async def _borrar(conn, sql, *args):
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Tenants A y B. En A: owner, staff completo (ver/gestionar/anonimizar), staff con los permisos viejos,
    solo-lectura y sin permisos."""
    sufijo = uuid.uuid4().hex[:8]
    ta, tb = uuid.uuid4(), uuid.uuid4()
    for t, n in ((ta, "a"), (tb, "b")):
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n.upper(), f"it-cli-{n}-{sufijo}"
        )
    usuarios = {}
    permisos = {
        "owner": None,
        "completo": ("view_bookings", "manage_customers", "delete_customers"),
        "viejo": ("view_bookings", "approve_bookings", "manage_staff"),
        "lectura": ("view_bookings",),
        "sin": (),
        "b_owner": None,
    }
    for nombre, claves in permisos.items():
        auth_id = uuid.uuid4()
        tenant = tb if nombre == "b_owner" else ta
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", auth_id, f"{auth_id.hex[:12]}@prueba.invalid"
        )
        tu = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            tenant,
            auth_id,
            claves is None,
        )
        for clave in claves or ():
            await db_superuser.execute(
                "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, $2)", tu, clave
            )
        usuarios[nombre] = auth_id
    datos = {"a": ta, "b": tb, **usuarios}
    try:
        yield datos
    finally:
        tenants = [ta, tb]
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            for tabla in (
                "bookings",
                "booking_packs",
                "notifications_log",
                "tenant_integrations",
                "professionals",
                "services",
                "end_customers",
                "tenant_user_permissions",
            ):
                await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from auth.users where id = any($1::uuid[])", list(usuarios.values()))


@pytest.fixture
async def dashboard(conectar_como):
    return await conectar_como("dashboard_service")


def _h(escenario, quien, tenant="a", escribe=True):
    owner = quien in ("owner", "b_owner")
    h = {
        "Cookie": "dash_acceso="
        + token(
            user_id=escenario[quien],
            tenant_id=escenario[tenant],
            is_owner=owner,
            aal="aal2" if owner else "aal1",
        )
    }
    if escribe:
        h["Origin"] = ORIGEN
    return h


async def _alta(app, escenario, quien="owner", tenant="a", phone=TEL_A, name="Cliente de prueba", **extra):
    return await http(app).post(
        "/clientes", json={"phone": phone, "name": name, **extra}, headers=_h(escenario, quien, tenant)
    )


async def _con_turnos(db, tenant, cliente):
    """Un servicio, un profesional con Google Calendar, un turno pending futuro y un confirmed con evento."""
    prof, serv = uuid.uuid4(), uuid.uuid4()
    pendiente, confirmado = uuid.uuid4(), uuid.uuid4()
    inicio = datetime.now(timezone.utc) + timedelta(days=30)  # noqa: UP017
    evento = f"evt-{uuid.uuid4().hex[:8]}"
    await db.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')", prof, tenant
    )
    await db.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'Corte', 30, 10)",
        serv,
        tenant,
    )
    integracion = await db.fetchval(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2) returning id",
        tenant,
        prof,
    )
    await db.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'pending', $8)",
        pendiente,
        tenant,
        cliente,
        serv,
        prof,
        inicio,
        inicio + timedelta(minutes=30),
        inicio - timedelta(days=1),
    )
    await db.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, google_calendar_event_id) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed', $8)",
        confirmado,
        tenant,
        cliente,
        serv,
        prof,
        inicio + timedelta(days=1),
        inicio + timedelta(days=1, minutes=30),
        evento,
    )
    return {"pendiente": pendiente, "confirmado": confirmado, "evento": evento, "integracion": integracion}


# --- alta, listado, búsqueda, dedup ---------------------------------------------------------------------------


async def test_alta_manual_lista_busca_y_deduplica_por_telefono(dashboard, escenario):
    calendario = CalendarioReal()
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(calendario))
    r = await _alta(app, escenario)
    assert r.status_code == 201, r.text
    cliente = r.json()
    assert cliente["phone"] == TEL_A_E164
    assert cliente["sin_cliente_identificable"] is False

    lista = await http(app).get("/clientes", headers=_h(escenario, "owner", escribe=False))
    assert [x["id"] for x in lista.json()["clientes"]] == [cliente["id"]]
    for busqueda in ("5550-0101".replace("-", ""), "Cliente de"):
        r = await http(app).get(
            "/clientes", params={"busqueda": busqueda}, headers=_h(escenario, "owner", escribe=False)
        )
        assert [x["id"] for x in r.json()["clientes"]] == [cliente["id"]], busqueda
    r = await http(app).get(
        "/clientes", params={"busqueda": "no-existe"}, headers=_h(escenario, "owner", escribe=False)
    )
    assert r.json()["clientes"] == []

    repetido = await _alta(app, escenario, phone="+5491155500101", name="Otro nombre")
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["cliente_id"] == cliente["id"]


async def _en_tx_del_bot(bot, tenant, funcion, *args):
    """Corre una función del repositorio del bot como `bot_service`, con el contexto de tenant de su transacción."""
    async with bot.transaction():
        await bot.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
        return await funcion(bot, tenant, *args)


async def _telegram_de(conn, tenant, ficha):
    """Identificador de Telegram de la ficha, según `canales_cliente`."""
    return await conn.fetchval(
        "select identificador from public.canales_cliente "
        "where tenant_id = $1 and end_customer_id = $2 and canal = 'telegram'",
        tenant,
        ficha,
    )


@pytest.mark.parametrize("alta_manual", ["011 15-5550-0101", "+54 11 5550-0101", "11 5550 0101", TEL_A])
async def test_el_alta_manual_y_el_contacto_de_telegram_se_vinculan_como_la_misma_persona(
    dashboard, conectar_como, escenario, alta_manual
):
    """Dedup §2.8 de punta a punta: una sola normalización para el dashboard y el bot, sin duplicar la fila."""
    from turnos_bot.identidad import repositorio
    from turnos_telefonos import normalizar_e164

    bot = await conectar_como("bot_service")
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    r = await _alta(app, escenario, phone=alta_manual)
    assert r.status_code == 201, r.text
    assert r.json()["phone"] == TEL_A_E164

    usuario = f"tg-{uuid.uuid4().hex[:8]}"
    vinculado = await _en_tx_del_bot(
        bot, escenario["a"], repositorio.vincular_alta_manual, "telegram", usuario, normalizar_e164("5491155500101")
    )
    assert vinculado is not None
    assert str(vinculado.id) == r.json()["id"]
    assert await _en_tx_del_bot(bot, escenario["a"], _telegram_de, vinculado.id) == usuario


async def test_el_cliente_creado_por_el_bot_bloquea_el_alta_manual_del_mismo_numero_escrito_distinto(
    dashboard, conectar_como, escenario
):
    from turnos_bot.identidad import repositorio
    from turnos_telefonos import normalizar_e164

    bot = await conectar_como("bot_service")
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    creado = await _en_tx_del_bot(
        bot,
        escenario["a"],
        repositorio.crear,
        "telegram",
        f"tg-{uuid.uuid4().hex[:8]}",
        normalizar_e164("5491155500101"),
    )
    repetido = await _alta(app, escenario, phone="011 15-5550-0101")
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["cliente_id"] == str(creado.id)


async def test_el_mismo_telefono_puede_existir_en_otro_tenant_y_cada_uno_ve_solo_lo_suyo(dashboard, escenario):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    de_a = (await _alta(app, escenario, "owner", "a")).json()
    de_b = await _alta(app, escenario, "b_owner", "b")
    assert de_b.status_code == 201
    lista_b = await http(app).get("/clientes", headers=_h(escenario, "b_owner", "b", escribe=False))
    assert [x["id"] for x in lista_b.json()["clientes"]] == [de_b.json()["id"]]
    # Un cliente del tenant A no se ve ni se edita desde el B.
    r = await http(app).get(f"/clientes/{de_a['id']}", headers=_h(escenario, "b_owner", "b", escribe=False))
    assert r.status_code == 404
    r = await http(app).patch(f"/clientes/{de_a['id']}", json={"name": "Pisado"}, headers=_h(escenario, "b_owner", "b"))
    assert r.status_code == 404


async def test_extra_data_respeta_los_campos_que_configuro_el_tenant(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    r = await _alta(app, escenario, extra_data={"alergias": "latex"})
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "extra_data_invalido"
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1",
        escenario["a"],
        json.dumps({"end_customer_extra_fields": [{"key": "alergias", "label": "Alergias"}]}),
    )
    r = await _alta(app, escenario, extra_data={"alergias": "latex"})
    assert r.status_code == 201, r.text
    assert r.json()["extra_data"] == {"alergias": "latex"}


async def test_dashboard_service_no_puede_borrar_end_customers(dashboard, escenario):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    cliente = (await _alta(app, escenario)).json()
    async with dashboard.transaction():
        await dashboard.execute("select set_config('app.current_tenant_id', $1, true)", str(escenario["a"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await dashboard.execute("delete from public.end_customers where id = $1", uuid.UUID(cliente["id"]))


# --- permisos -------------------------------------------------------------------------------------------------


async def test_los_permisos_de_cada_endpoint_se_verifican_en_la_base(dashboard, escenario):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    cliente = (await _alta(app, escenario)).json()
    cid = cliente["id"]

    # Sin permisos: nada.
    sin = _h(escenario, "sin")
    assert (await http(app).get("/clientes", headers={**sin, "Origin": ORIGEN})).status_code == 403
    assert (await http(app).post("/clientes", json={"phone": TEL_A, "name": "X"}, headers=sin)).status_code == 403

    # Solo lectura (view_bookings): ve, pero no crea, ni edita, ni anonimiza.
    lectura = _h(escenario, "lectura")
    assert (await http(app).get("/clientes", headers=lectura)).status_code == 200
    assert (await http(app).get(f"/clientes/{cid}", headers=lectura)).status_code == 200
    assert (
        await http(app).post("/clientes", json={"phone": "+5491155500111", "name": "X"}, headers=lectura)
    ).status_code == 403
    assert (await http(app).patch(f"/clientes/{cid}", json={"name": "X"}, headers=lectura)).status_code == 403
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=lectura)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert (await http(app).get(f"/clientes/{cid}", headers=lectura)).json()["phone"] == TEL_A_E164  # intacto

    # Con los tres permisos: gestiona y anonimiza.
    completo = _h(escenario, "completo")
    assert (
        await http(app).patch(f"/clientes/{cid}", json={"name": "Nombre corregido"}, headers=completo)
    ).status_code == 200
    assert (await http(app).post(f"/clientes/{cid}/anonimizar", headers=completo)).status_code == 200


# --- anonimización ---------------------------------------------------------------------------------------------


async def test_anonimizar_un_cliente_de_otro_tenant_da_404_y_no_lo_toca(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    de_a = (await _alta(app, escenario, "owner", "a")).json()
    r = await http(app).post(f"/clientes/{de_a['id']}/anonimizar", headers=_h(escenario, "b_owner", "b"))
    assert r.status_code == 404
    fila = await db_superuser.fetchrow(
        "select phone, name, deleted_at from public.end_customers where id = $1", uuid.UUID(de_a["id"])
    )
    assert fila["phone"] == TEL_A_E164
    assert fila["deleted_at"] is None
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.audit_log "
            "where action = 'anonymize_end_customer' and tenant_id = any($1::uuid[])",
            [escenario["a"], escenario["b"]],
        )
        == 0
    )


async def test_anonimizar_conserva_los_confirmed_cancela_los_pending_y_limpia_el_calendario(
    dashboard, escenario, db_superuser
):
    calendario = CalendarioReal()
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(calendario))
    cliente = (await _alta(app, escenario)).json()
    cid = uuid.UUID(cliente["id"])
    turnos = await _con_turnos(db_superuser, escenario["a"], cid)

    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert r.json()["sin_cliente_identificable"] is True
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 1, "no_limpiados": 0}

    fila = await db_superuser.fetchrow(
        "select phone, name, extra_data, deleted_at from public.end_customers where id = $1", cid
    )
    assert fila["phone"] is None and fila["name"] is None and fila["deleted_at"] is not None
    estados = {
        r["id"]: r["status"]
        for r in await db_superuser.fetch("select id, status from public.bookings where end_customer_id = $1", cid)
    }
    assert estados[turnos["confirmado"]] == "confirmed"
    assert estados[turnos["pendiente"]] == "cancelled"
    assert calendario.pares == [(turnos["integracion"], turnos["evento"])]

    # El borrado deja constancia en audit_log (la hace la función SQL), con el tenant_user como actor.
    audit = await db_superuser.fetchrow(
        "select actor_type, actor_id from public.audit_log where action = 'anonymize_end_customer' and entity_id = $1",
        cid,
    )
    assert audit["actor_type"] == "staff"
    assert audit["actor_id"] == escenario["owner"]

    # El detalle ya lo marca como "sin cliente identificable".
    d = await http(app).get(f"/clientes/{cid}", headers=_h(escenario, "owner", escribe=False))
    assert d.json()["sin_cliente_identificable"] is True
    assert d.json()["phone"] is None
    # Editarlo después de anonimizado no se permite; repetir la anonimización es idempotente.
    assert (
        await http(app).patch(f"/clientes/{cid}", json={"name": "X"}, headers=_h(escenario, "owner"))
    ).status_code == 409
    assert (await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escenario, "owner"))).status_code == 200


async def test_los_eventos_no_limpiados_se_registran_en_audit_log_sin_pii(dashboard, escenario, db_superuser):
    calendario = CalendarioReal()
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(calendario))
    cliente = (await _alta(app, escenario)).json()
    cid = uuid.UUID(cliente["id"])
    turnos = await _con_turnos(db_superuser, escenario["a"], cid)
    calendario.fallidos = [EventoNoLimpiado(turnos["integracion"], turnos["evento"], "token_vencido")]

    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escenario, "owner"))
    assert r.status_code == 200
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 1, "no_limpiados": 1}
    filas = await db_superuser.fetch(
        "select entity, entity_id, metadata from public.audit_log where action = 'calendar_scrub_failed' "
        "and tenant_id = $1",
        escenario["a"],
    )
    assert len(filas) == 1
    assert filas[0]["entity_id"] == cid
    assert json.loads(filas[0]["metadata"]) == {"event_id": turnos["evento"], "motivo": "token_vencido"}


async def test_sin_google_configurado_se_anonimiza_igual_y_queda_el_motivo_de_configuracion(
    dashboard, escenario, db_superuser, monkeypatch
):
    for v in VARIABLES_GOOGLE:
        monkeypatch.delenv(v, raising=False)
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard))  # fábrica por defecto (entorno)
    cliente = (await _alta(app, escenario)).json()
    cid = uuid.UUID(cliente["id"])
    turnos = await _con_turnos(db_superuser, escenario["a"], cid)

    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert await db_superuser.fetchval("select deleted_at is not null from public.end_customers where id = $1", cid)
    filas = await db_superuser.fetch(
        "select metadata from public.audit_log where action = 'calendar_scrub_failed' and tenant_id = $1",
        escenario["a"],
    )
    assert [json.loads(f["metadata"]) for f in filas] == [
        {"event_id": turnos["evento"], "motivo": "configuracion_calendar"}
    ]


async def test_los_permisos_viejos_ya_no_alcanzan_para_gestionar_ni_anonimizar(dashboard, escenario):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    cid = (await _alta(app, escenario)).json()["id"]
    viejo = _h(escenario, "viejo")
    assert (await http(app).get(f"/clientes/{cid}", headers=viejo)).status_code == 200
    r = await http(app).post("/clientes", json={"phone": "+5491155500112", "name": "X"}, headers=viejo)
    assert r.status_code == 403
    assert (await http(app).patch(f"/clientes/{cid}", json={"name": "X"}, headers=viejo)).status_code == 403
    assert (await http(app).post(f"/clientes/{cid}/anonimizar", headers=viejo)).status_code == 403
    owner = _h(escenario, "owner")
    assert (await http(app).patch(f"/clientes/{cid}", json={"name": "Del owner"}, headers=owner)).status_code == 200
    assert (await http(app).post(f"/clientes/{cid}/anonimizar", headers=owner)).status_code == 200


async def test_alta_y_edicion_de_clientes_se_auditan_sin_valores_personales(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    cliente = (await _alta(app, escenario)).json()
    r = await http(app).patch(
        f"/clientes/{cliente['id']}", json={"name": "Nombre Nuevo"}, headers=_h(escenario, "owner")
    )
    assert r.status_code == 200, r.text
    filas = await db_superuser.fetch(
        "select action, entity_id, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action in ('customer_created', 'customer_updated') order by created_at, id",
        escenario["a"],
    )
    assert [f["action"] for f in filas] == ["customer_created", "customer_updated"]
    assert all(str(f["entity_id"]) == cliente["id"] for f in filas)
    assert [json.loads(f["metadata"]) for f in filas] == [{"campos": ["name", "phone"]}, {"campos": ["name"]}]
    volcado = " ".join(f["metadata"] for f in filas)
    assert "Nombre Nuevo" not in volcado and TEL_A_E164 not in volcado


# --- email y canales -------------------------------------------------------------------------------------------


async def test_el_email_es_unico_por_tenant_sin_distinguir_mayusculas_y_aislado_entre_tenants(dashboard, escenario):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    r = await _alta(app, escenario, email=" Ana@Correo.com ")
    assert r.status_code == 201, r.text
    ficha = r.json()["id"]
    assert r.json()["email"] == "ana@correo.com"

    r = await _alta(app, escenario, phone="+54 9 11 5550-0102", name="Otra", email="ANA@correo.com")
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "codigo": "cliente_existente",
        "mensaje": "Ya existe un cliente con ese email",
        "cliente_id": ficha,
    }

    # Editar otra ficha con ese mismo email también choca, y sin 500.
    r = await _alta(app, escenario, phone="+54 9 11 5550-0103", name="Tercera")
    otra = r.json()["id"]
    r = await http(app).patch(f"/clientes/{otra}", json={"email": "ana@correo.com"}, headers=_h(escenario, "owner"))
    assert r.status_code == 409
    assert r.json()["detail"]["cliente_id"] == ficha

    # Otro tenant: el mismo email es otra persona, y no puede ver ni editar la ficha del tenant A.
    r = await _alta(app, escenario, quien="b_owner", tenant="b", email="ana@correo.com")
    assert r.status_code == 201, r.text
    r = await http(app).get(f"/clientes/{ficha}", headers=_h(escenario, "b_owner", "b", escribe=False))
    assert r.status_code == 404
    r = await http(app).patch(
        f"/clientes/{ficha}", json={"email": "x@correo.com"}, headers=_h(escenario, "b_owner", "b")
    )
    assert r.status_code == 404


async def test_la_ficha_muestra_sus_canales_sin_el_identificador(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    r = await _alta(app, escenario, email="canales@correo.com")
    ficha = uuid.UUID(r.json()["id"])
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'telegram', '998877665544', now()), ($1, $2, 'email', 'canales@correo.com', null)",
        escenario["a"],
        ficha,
    )
    detalle = await http(app).get(f"/clientes/{ficha}", headers=_h(escenario, "owner", escribe=False))
    assert detalle.status_code == 200
    canales = {c["canal"]: c["verificado"] for c in detalle.json()["canales"]}
    assert canales == {"telegram": True, "email": False}
    assert "998877665544" not in detalle.text
    listado = await http(app).get("/clientes", headers=_h(escenario, "owner", escribe=False))
    mio = next(c for c in listado.json()["clientes"] if c["id"] == str(ficha))
    assert mio["email"] == "canales@correo.com" and len(mio["canales"]) == 2
    assert "998877665544" not in listado.text


# --- unir dos fichas ---------------------------------------------------------------------------------------------


async def _ficha(db, tenant, telefono, nombre, email=None, canales=()):
    """Una ficha del tenant con sus canales `(canal, identificador, verificado)`."""
    ficha = await db.fetchval(
        "insert into public.end_customers (tenant_id, phone, name, email) values ($1, $2, $3, $4) returning id",
        tenant,
        telefono,
        nombre,
        email,
    )
    for canal, identificador, verificado in canales:
        await db.execute(
            "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
            "values ($1, $2, $3, $4, case when $5::boolean then now() end)",
            tenant,
            ficha,
            canal,
            identificador,
            verificado,
        )
    return ficha


async def _turno(db, tenant, ficha, dia, estado="confirmed"):
    """Un turno de la ficha (con su servicio y profesional propios) el día `dia` de marzo de 2031."""
    prof, serv, turno = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute("insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", prof, tenant)
    await db.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'Corte', 30, 10)",
        serv,
        tenant,
    )
    inicio = datetime(2031, 3, dia, 12, tzinfo=timezone.utc)  # noqa: UP017
    await db.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at) values ($1, $2, $3, $4, $5, tstzrange($6, $7), $8, "
        "case when $8 = 'pending' then $6 - interval '1 hour' end)",
        turno,
        tenant,
        ficha,
        serv,
        prof,
        inicio,
        inicio + timedelta(minutes=30),
        estado,
    )
    return turno, prof, serv, inicio


async def _unir(app, escenario, principal, absorbida, quien="completo", tenant="a"):
    return await http(app).post(
        f"/clientes/{principal}/unir", json={"absorber_id": str(absorbida)}, headers=_h(escenario, quien, tenant)
    )


async def test_unir_mueve_todo_a_la_principal_y_anonimiza_la_absorbida(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    a = escenario["a"]
    principal = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id, phone, name, email) "
        "values ($1, 'telegram', 'tg-it-principal', '+5491155500201', 'Principal', "
        "'principal@correo.com') returning id",
        a,
    )
    absorbida = await _ficha(
        db_superuser, a, "+5491155500202", "Absorbida", "absorbida@correo.com", [("email", "viejo@correo.com", True)]
    )
    turno, prof, serv, inicio = await _turno(db_superuser, a, absorbida, 10)
    await db_superuser.execute(
        "update public.bookings set starts_at_propuesto = $2, ends_at_propuesto = $3, "
        "cambio_pendiente_expira_at = now() + interval '1 day' where id = $1",
        turno,
        inicio + timedelta(hours=2),
        inicio + timedelta(hours=2, minutes=30),
    )
    pack = await db_superuser.fetchval(
        "insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin) "
        "values ($1, $2, $3, 5, 'manual_grant') returning id",
        a,
        absorbida,
        serv,
    )
    aviso = await db_superuser.fetchval(
        "insert into public.notification_outbox (tenant_id, end_customer_id, channel, template) "
        "values ($1, $2, 'telegram', 'turno_confirmado') returning id",
        a,
        absorbida,
    )

    r = await _unir(app, escenario, principal, absorbida)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["cliente"]["id"] == str(principal)
    assert cuerpo["cliente"]["email"] == "principal@correo.com"
    assert cuerpo["cliente"]["phone"] == "+5491155500201"
    assert cuerpo["absorbida_id"] == str(absorbida)
    assert (cuerpo["turnos"], cuerpo["packs"], cuerpo["canales"], cuerpo["avisos"]) == (1, 1, 1, 1)
    assert {x["canal"] for x in cuerpo["cliente"]["canales"]} == {"telegram", "email"}
    assert "viejo@correo.com" not in r.text

    fila = await db_superuser.fetchrow(
        "select end_customer_id, time_range, starts_at_propuesto from public.bookings where id = $1", turno
    )
    assert fila["end_customer_id"] == principal
    assert fila["starts_at_propuesto"] == inicio + timedelta(hours=2)
    assert (fila["time_range"].lower, fila["time_range"].upper) == (inicio, inicio + timedelta(minutes=30))
    pack_movido = await db_superuser.fetchval("select end_customer_id from public.booking_packs where id = $1", pack)
    assert pack_movido == principal
    estado_aviso = await db_superuser.fetchrow(
        "select end_customer_id, status from public.notification_outbox where id = $1", aviso
    )
    assert (estado_aviso["end_customer_id"], estado_aviso["status"]) == (principal, "pending")
    anonimizada = await db_superuser.fetchrow(
        "select phone, name, email, deleted_at from public.end_customers where id = $1", absorbida
    )
    assert anonimizada["deleted_at"] is not None
    assert (anonimizada["phone"], anonimizada["name"], anonimizada["email"]) == (None, None, None)

    audit = await db_superuser.fetchrow(
        "select actor_type, actor_id, tenant_id, entity_id, metadata from public.audit_log "
        "where action = 'customers_merged' and tenant_id = $1",
        a,
    )
    assert (audit["actor_type"], audit["actor_id"], audit["entity_id"]) == ("staff", escenario["completo"], principal)
    metadata = json.loads(audit["metadata"]) if isinstance(audit["metadata"], str) else audit["metadata"]
    assert metadata["absorbida_id"] == str(absorbida)
    assert "correo.com" not in json.dumps(metadata)


async def test_la_principal_sin_email_ni_telefono_toma_los_de_la_absorbida(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    principal = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, name) values ($1, 'Sin datos') returning id", escenario["a"]
    )
    absorbida = await _ficha(db_superuser, escenario["a"], "+5491155500203", "Con datos", "con.datos@correo.com")
    r = await _unir(app, escenario, principal, absorbida)
    assert r.status_code == 200, r.text
    assert r.json()["cliente"]["phone"] == "+5491155500203"
    assert r.json()["cliente"]["email"] == "con.datos@correo.com"
    marcas = await _marcas_de_descarte(db_superuser, principal)
    assert marcas == (False, False)
    # y la unicidad por email sigue valiendo: nadie más puede tomarlo
    otro = await _alta(app, escenario, phone="+5491155500204", email="con.datos@correo.com")
    assert otro.status_code == 409
    assert otro.json()["detail"]["cliente_id"] == str(principal)


async def test_unir_sin_el_permiso_de_gestionar_clientes_es_403_y_no_toca_nada(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    principal = await _ficha(db_superuser, escenario["a"], "+5491155500205", "Uno")
    absorbida = await _ficha(db_superuser, escenario["a"], "+5491155500206", "Dos")
    for quien in ("lectura", "sin", "viejo"):
        r = await _unir(app, escenario, principal, absorbida, quien)
        assert r.status_code == 403, quien
        assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert await db_superuser.fetchval("select deleted_at from public.end_customers where id = $1", absorbida) is None
    assert not await db_superuser.fetchval("select count(*) from public.audit_log where action = 'customers_merged'")


async def test_unir_con_una_ficha_de_otro_tenant_da_404_indistinguible_de_una_inexistente(
    dashboard, escenario, db_superuser
):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    de_a = await _ficha(db_superuser, escenario["a"], "+5491155500207", "De A", "a@correo.com")
    de_b = await _ficha(db_superuser, escenario["b"], "+5491155500208", "De B", "b@correo.com")
    turno_b, *_ = await _turno(db_superuser, escenario["b"], de_b, 11)

    ajena_absorbida = await _unir(app, escenario, de_a, de_b, "owner", "a")
    ajena_principal = await _unir(app, escenario, de_b, de_a, "owner", "a")
    inexistente = await _unir(app, escenario, de_a, uuid.uuid4(), "owner", "a")
    desde_b = await _unir(app, escenario, de_a, de_b, "b_owner", "b")
    assert {r.status_code for r in (ajena_absorbida, ajena_principal, inexistente, desde_b)} == {404}
    assert ajena_absorbida.json() == ajena_principal.json() == inexistente.json() == desde_b.json()

    assert await db_superuser.fetchval("select end_customer_id from public.bookings where id = $1", turno_b) == de_b
    ficha_b = await db_superuser.fetchrow("select deleted_at, email from public.end_customers where id = $1", de_b)
    assert ficha_b["deleted_at"] is None and ficha_b["email"] == "b@correo.com"
    assert not await db_superuser.fetchval("select count(*) from public.audit_log where action = 'customers_merged'")


async def test_los_bordes_de_la_union_son_4xx_y_no_cambian_nada(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    a = escenario["a"]
    una = await _ficha(db_superuser, a, "+5491155500209", "Una", canales=[("telegram", "tg-uno", True)])
    otra = await _ficha(db_superuser, a, "+5491155500210", "Otra", canales=[("telegram", "tg-otra", True)])
    anonimizada = await _ficha(db_superuser, a, "+5491155500211", "Anonimizada")
    turno, *_ = await _turno(db_superuser, a, otra, 12)
    r = await http(app).post(f"/clientes/{anonimizada}/anonimizar", headers=_h(escenario, "owner"))
    assert r.status_code == 200

    misma = await _unir(app, escenario, una, una)
    assert (misma.status_code, misma.json()["detail"]["codigo"]) == (422, "union_misma_ficha")
    conflicto = await _unir(app, escenario, una, otra)
    assert (conflicto.status_code, conflicto.json()["detail"]["codigo"]) == (409, "canal_en_conflicto")
    assert "tg-" not in conflicto.text
    absorbida_anonimizada = await _unir(app, escenario, una, anonimizada)
    assert (absorbida_anonimizada.status_code, absorbida_anonimizada.json()["detail"]["codigo"]) == (
        409,
        "cliente_anonimizado",
    )
    principal_anonimizada = await _unir(app, escenario, anonimizada, una)
    assert principal_anonimizada.status_code == 409

    # el conflicto no movió nada
    assert await db_superuser.fetchval("select end_customer_id from public.bookings where id = $1", turno) == otra
    assert await db_superuser.fetchval("select deleted_at from public.end_customers where id = $1", otra) is None
    assert not await db_superuser.fetchval("select count(*) from public.audit_log where action = 'customers_merged'")


async def test_dos_uniones_cruzadas_a_la_vez_no_corrompen_nada(dashboard, conectar_como, escenario, db_superuser):
    otra_conexion = await conectar_como("dashboard_service")
    app1, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    app2, _, _ = app_clientes(pool=_PoolDeUnaConexion(otra_conexion), fabrica=fabrica_de(CalendarioReal()))
    a = escenario["a"]
    una = await _ficha(db_superuser, a, "+5491155500212", "Una", "una@correo.com")
    otra = await _ficha(db_superuser, a, "+5491155500213", "Otra", "otra@correo.com")
    turno_una, *_ = await _turno(db_superuser, a, una, 13)
    turno_otra, *_ = await _turno(db_superuser, a, otra, 14)

    r1, r2 = await asyncio.gather(_unir(app1, escenario, una, otra), _unir(app2, escenario, otra, una))
    assert sorted((r1.status_code, r2.status_code)) == [200, 409], (r1.text, r2.text)

    vivas = await db_superuser.fetch(
        "select id from public.end_customers where id = any($1::uuid[]) and deleted_at is null", [una, otra]
    )
    assert len(vivas) == 1
    sobreviviente = vivas[0]["id"]
    for turno in (turno_una, turno_otra):
        duenio = await db_superuser.fetchval("select end_customer_id from public.bookings where id = $1", turno)
        assert duenio == sobreviviente
    registros = await db_superuser.fetchval(
        "select count(*) from public.audit_log where action = 'customers_merged' and tenant_id = $1", a
    )
    assert registros == 1


async def _marcas_de_descarte(db, principal):
    metadata = await db.fetchval(
        "select metadata from public.audit_log where action = 'customers_merged' and entity_id = $1", principal
    )
    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    return metadata["telefono_descartado"], metadata["email_descartado"]


async def test_el_mismo_telefono_en_las_dos_fichas_no_cuenta_como_dato_descartado(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    principal = await _ficha(db_superuser, escenario["a"], "+5491155500301", "Una")
    absorbida = await _ficha(db_superuser, escenario["a"], "+5491155500301", "Otra")
    r = await _unir(app, escenario, principal, absorbida)
    assert r.status_code == 200, r.text
    assert await _marcas_de_descarte(db_superuser, principal) == (False, False)


async def test_los_datos_distintos_si_se_marcan_como_descartados(dashboard, escenario, db_superuser):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    principal = await _ficha(db_superuser, escenario["a"], "+5491155500302", "Una", "una@correo.com")
    absorbida = await _ficha(db_superuser, escenario["a"], "+5491155500303", "Otra", "otra@correo.com")
    r = await _unir(app, escenario, principal, absorbida)
    assert r.status_code == 200, r.text
    assert await _marcas_de_descarte(db_superuser, principal) == (True, True)


async def test_los_avisos_pendientes_de_cualquier_canal_pasan_a_la_principal_y_siguen_pendientes(
    dashboard, escenario, db_superuser
):
    app, _, _ = app_clientes(pool=_PoolDeUnaConexion(dashboard), fabrica=fabrica_de(CalendarioReal()))
    a = escenario["a"]
    principal = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, 'whatsapp', 'wa-it-1', '+5491155500304', 'Con WhatsApp') returning id",
        a,
    )
    absorbida = await _ficha(db_superuser, a, "+5491155500305", "Con avisos")
    avisos = {}
    for canal in ("telegram", "whatsapp"):
        avisos[canal] = await db_superuser.fetchval(
            "insert into public.notification_outbox (tenant_id, end_customer_id, channel, template) "
            "values ($1, $2, $3, 'turno_confirmado') returning id",
            a,
            absorbida,
            canal,
        )
    r = await _unir(app, escenario, principal, absorbida)
    assert r.status_code == 200, r.text
    filas = {
        f["channel"]: f
        for f in await db_superuser.fetch(
            "select channel, status, last_error from public.notification_outbox where end_customer_id = $1", principal
        )
    }
    # los avisos pendientes pasan a la principal y se entregan por todos sus canales: ninguno se descarta
    assert {c: (f["status"], f["last_error"]) for c, f in filas.items()} == {
        "telegram": ("pending", None),
        "whatsapp": ("pending", None),
    }
