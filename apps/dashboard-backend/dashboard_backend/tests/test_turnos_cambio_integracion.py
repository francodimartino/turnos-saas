"""Resolver cambios de horario pendientes contra Postgres real con el rol `dashboard_service`.

Cubre lo que una conexión falsa no puede: el horario que queda en la base, el choque con otro turno, el vencimiento
bajo lock, dos resoluciones a la vez, el `audit_log` con el actor, el aviso en el outbox y el aislamiento entre dos
negocios con RLS. Datos ficticios; Calendar y Telegram van simulados (sin red).
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_turnos import app_con_pool  # noqa: E402

BASE = datetime.now(timezone.utc).replace(microsecond=0, second=0, minute=0) + timedelta(days=30)  # noqa: UP017
PROPUESTO = BASE + timedelta(days=2)
DURACION = timedelta(minutes=30)


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class _PoolDeVarias:
    """Pool mínimo con conexiones distintas: lo que hace falta para probar requests realmente simultáneos."""

    def __init__(self, conns):
        self._libres: asyncio.Queue = asyncio.Queue()
        for c in conns:
            self._libres.put_nowait(c)

    @asynccontextmanager
    async def acquire(self):
        conn = await self._libres.get()
        try:
            yield conn
        finally:
            self._libres.put_nowait(conn)


async def _borrar(conn, sql, *args):
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


async def _tenant(db, sufijo, letra):
    """Un negocio con su cliente (Telegram verificado), servicio y profesional que lo ofrece."""
    t = uuid.uuid4()
    await db.execute(
        "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, letra.upper(), f"it-cam-{letra}-{sufijo}"
    )
    cliente, servicio, profesional = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    chat = f"chat-{uuid.uuid4().hex[:8]}"
    await db.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, $2, 'telegram', $3, $4, 'Cliente de prueba')",
        cliente,
        t,
        chat,
        f"+54911555{uuid.uuid4().int % 10**7:07d}",
    )
    await db.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'telegram', $3, now())",
        t,
        cliente,
        chat,
    )
    await db.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'Corte', 30, 10)",
        servicio,
        t,
    )
    await db.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')", profesional, t
    )
    await db.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
        "values ($1, $2, $3, 'approval')",
        t,
        profesional,
        servicio,
    )
    return {"tenant": t, "cliente": cliente, "servicio": servicio, "profesional": profesional}


@pytest.fixture
async def escenario(db_superuser):
    """Negocios A y B. En A: owner, `aprobador` (solo approve_bookings), `lectura` y `sin`. En B: su owner."""
    sufijo = uuid.uuid4().hex[:8]
    a = await _tenant(db_superuser, sufijo, "a")
    b = await _tenant(db_superuser, sufijo, "b")
    permisos = {
        "owner": (a, None),
        "aprobador": (a, ("approve_bookings",)),
        "lectura": (a, ("view_bookings",)),
        "sin": (a, ()),
        "b_owner": (b, None),
    }
    usuarios: dict[str, uuid.UUID] = {}
    for nombre, (datos, claves) in permisos.items():
        auth_id = uuid.uuid4()
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", auth_id, f"{auth_id.hex[:12]}@prueba.invalid"
        )
        tu = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            datos["tenant"],
            auth_id,
            claves is None,
        )
        for clave in claves or ():
            await db_superuser.execute(
                "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, $2)", tu, clave
            )
        usuarios[nombre] = auth_id
    tenants = [a["tenant"], b["tenant"]]
    try:
        yield {"a": a, "b": b, "usuarios": usuarios}
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            for tabla in (
                "bookings",
                "notifications_log",
                "notification_outbox",
                "canales_cliente",
                "professional_services",
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


def _h(escenario, quien, escribe=True):
    owner = quien in ("owner", "b_owner")
    tenant = escenario["b" if quien == "b_owner" else "a"]["tenant"]
    h = {
        "Cookie": "dash_acceso="
        + token(
            user_id=escenario["usuarios"][quien],
            tenant_id=tenant,
            is_owner=owner,
            aal="aal2" if owner else "aal1",
        )
    }
    if escribe:
        h["Origin"] = ORIGEN
    return h


async def _confirmado(db, datos, inicio=BASE):
    turno = uuid.uuid4()
    await db.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
        "values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed')",
        turno,
        datos["tenant"],
        datos["cliente"],
        datos["servicio"],
        datos["profesional"],
        inicio,
        inicio + DURACION,
    )
    return turno


async def _con_cambio(db, datos, *, inicio=BASE, propuesto=PROPUESTO, expira=None):
    """Un turno `confirmed` con un cambio pendiente. Por defecto vigente: vence cuando empieza el turno."""
    turno = await _confirmado(db, datos, inicio)
    await db.execute(
        "update public.bookings set starts_at_propuesto = $2, ends_at_propuesto = $3, "
        "cambio_pendiente_expira_at = $4 where id = $1",
        turno,
        propuesto,
        propuesto + DURACION,
        expira or inicio - timedelta(hours=1),
    )
    return turno


async def _fila(db, turno):
    return await db.fetchrow(
        "select status, lower(time_range) as inicio, upper(time_range) as fin, starts_at_propuesto, "
        "ends_at_propuesto, cambio_pendiente_expira_at, google_calendar_event_id from public.bookings where id = $1",
        turno,
    )


async def _auditoria(db, turno):
    return await db.fetch(
        "select action, actor_type, actor_id, metadata from public.audit_log where entity_id = $1 order by created_at",
        turno,
    )


async def _avisos(db, datos):
    return await db.fetch(
        "select template, status, channel, params from public.notification_outbox where end_customer_id = $1",
        datos["cliente"],
    )


async def _sin_efectos(db, datos, turno, esperado_inicio=BASE):
    """El turno sigue con su cambio pendiente y no quedó ni auditoría ni aviso."""
    fila = await _fila(db, turno)
    assert fila["inicio"] == esperado_inicio and fila["starts_at_propuesto"] is not None
    assert await _auditoria(db, turno) == []
    assert await _avisos(db, datos) == []


# --- aprobar y rechazar -----------------------------------------------------------------------------------------


async def test_aprobar_mueve_al_horario_nuevo_audita_con_el_actor_sincroniza_calendar_y_encola_el_aviso(
    dashboard, db_superuser, escenario
):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    r = await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "aprobador"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["starts_at_propuesto"] is None
    fila = await _fila(db_superuser, turno)
    assert fila["inicio"] == PROPUESTO and fila["fin"] == PROPUESTO + DURACION
    assert fila["starts_at_propuesto"] is None and fila["cambio_pendiente_expira_at"] is None
    assert fila["google_calendar_event_id"] == "evt-nuevo"
    assert calendario.llamadas[0].inicio == PROPUESTO
    (auditoria,) = await _auditoria(db_superuser, turno)
    assert auditoria["action"] == "booking_change_approved" and auditoria["actor_type"] == "staff"
    assert auditoria["actor_id"] == escenario["usuarios"]["aprobador"]
    assert json.loads(auditoria["metadata"])["canal"] == "staff"
    (aviso,) = await _avisos(db_superuser, a)
    assert aviso["template"] == "cambio_horario_aprobado" and aviso["status"] == "sent"
    assert json.loads(aviso["params"])["inicio"] == PROPUESTO.isoformat()
    assert len(bot.mensajes) == 1 and "aprobado" in bot.mensajes[0]["text"]


async def test_rechazar_conserva_el_horario_original_audita_y_encola_el_aviso(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    r = await http(app).post(f"/turnos/{turno}/cambio/rechazar", headers=_h(escenario, "aprobador"))
    assert r.status_code == 200, r.text
    fila = await _fila(db_superuser, turno)
    assert fila["status"] == "confirmed" and fila["inicio"] == BASE and fila["fin"] == BASE + DURACION
    assert fila["starts_at_propuesto"] is None and fila["cambio_pendiente_expira_at"] is None
    assert calendario.llamadas[0].inicio == BASE
    (auditoria,) = await _auditoria(db_superuser, turno)
    assert auditoria["action"] == "booking_change_rejected"
    assert auditoria["actor_id"] == escenario["usuarios"]["aprobador"]
    (aviso,) = await _avisos(db_superuser, a)
    assert aviso["template"] == "cambio_horario_rechazado" and json.loads(aviso["params"])["inicio"] == BASE.isoformat()
    assert len(bot.mensajes) == 1


async def test_con_otro_canal_verificado_el_aviso_queda_pendiente_y_no_se_envia(dashboard, db_superuser, escenario):
    a = escenario["a"]
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'email', $3, now())",
        a["tenant"],
        a["cliente"],
        f"{uuid.uuid4().hex[:8]}@prueba.invalid",
    )
    app, _, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    r = await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "pendiente_de_envio"
    (aviso,) = await _avisos(db_superuser, a)
    assert aviso["template"] == "cambio_horario_aprobado" and aviso["status"] == "pending"
    assert not bot.mensajes


# --- errores: ninguno es 500 y ninguno deja rastro ---------------------------------------------------------------


async def test_aprobar_un_cambio_vencido_es_409_pero_se_puede_rechazar(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a, expira=datetime.now(timezone.utc) - timedelta(hours=1))  # noqa: UP017
    r = await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_vencido"
    await _sin_efectos(db_superuser, a, turno)
    assert not calendario.llamadas and not bot.mensajes
    r = await http(app).post(f"/turnos/{turno}/cambio/rechazar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert (await _fila(db_superuser, turno))["inicio"] == BASE


async def test_aprobar_cuando_el_horario_original_ya_paso_es_409(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    pasado = datetime.now(timezone.utc).replace(microsecond=0, second=0, minute=0) - timedelta(days=2)  # noqa: UP017
    turno = await _con_cambio(
        db_superuser,
        a,
        inicio=pasado,
        expira=datetime.now(timezone.utc) + timedelta(days=1),  # noqa: UP017
    )
    r = await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_vencido"
    await _sin_efectos(db_superuser, a, turno, esperado_inicio=pasado)


async def test_un_horario_nuevo_ocupado_al_aprobar_es_409_horario_ocupado_y_nada_cambia(
    dashboard, db_superuser, escenario
):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    # Otro turno que ocupa el horario propuesto: sin triggers para dejarlo ahí aunque haya una propuesta encima.
    await _borrar(
        db_superuser,
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
        "values ($1, $2, $3, $4, tstzrange($5, $6), 'confirmed')",
        a["tenant"],
        a["cliente"],
        a["servicio"],
        a["profesional"],
        PROPUESTO,
        PROPUESTO + DURACION,
    )
    r = await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "horario_ocupado"
    await _sin_efectos(db_superuser, a, turno)
    assert not calendario.llamadas and not bot.mensajes
    # El negocio todavía puede rechazarlo.
    r = await http(app).post(f"/turnos/{turno}/cambio/rechazar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text


async def test_resolver_dos_veces_o_un_turno_sin_cambio_pendiente_es_409(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    assert (await http(app).post(f"/turnos/{turno}/cambio/aprobar", headers=_h(escenario, "owner"))).status_code == 200
    for accion in ("aprobar", "rechazar"):
        r = await http(app).post(f"/turnos/{turno}/cambio/{accion}", headers=_h(escenario, "owner"))
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_no_pendiente"
    # Un turno confirmado que nunca tuvo un cambio pendiente.
    sin_cambio = await _confirmado(db_superuser, a, BASE + timedelta(days=5))
    for accion in ("aprobar", "rechazar"):
        r = await http(app).post(f"/turnos/{sin_cambio}/cambio/{accion}", headers=_h(escenario, "owner"))
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_no_pendiente"
    assert await _auditoria(db_superuser, sin_cambio) == []
    assert len(await _auditoria(db_superuser, turno)) == 1
    assert len(await _avisos(db_superuser, a)) == 1


async def test_un_turno_inexistente_es_404(dashboard, escenario):
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    for accion in ("aprobar", "rechazar"):
        r = await http(app).post(f"/turnos/{uuid.uuid4()}/cambio/{accion}", headers=_h(escenario, "owner"))
        assert r.status_code == 404 and r.json()["detail"]["codigo"] == "turno_no_encontrado"


# --- permisos ---------------------------------------------------------------------------------------------------


async def test_sin_approve_bookings_es_403_y_no_cambia_nada(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _con_cambio(db_superuser, a)
    for quien in ("lectura", "sin"):
        for accion in ("aprobar", "rechazar"):
            r = await http(app).post(f"/turnos/{turno}/cambio/{accion}", headers=_h(escenario, quien))
            assert r.status_code == 403 and r.json()["detail"]["codigo"] == "permiso_denegado", (quien, accion)
    await _sin_efectos(db_superuser, a, turno)
    assert not calendario.llamadas and not bot.mensajes


async def test_listar_los_cambios_pendientes_exige_view_bookings_y_solo_trae_los_del_negocio(
    dashboard, db_superuser, escenario
):
    a, b = escenario["a"], escenario["b"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    con_cambio = await _con_cambio(db_superuser, a)
    await _confirmado(db_superuser, a, BASE + timedelta(days=5))  # confirmado sin cambio: no aparece
    ajeno = await _con_cambio(db_superuser, b)
    r = await http(app).get("/turnos", params={"cambio_pendiente": "true"}, headers=_h(escenario, "lectura", False))
    assert r.status_code == 200, r.text
    (turno,) = r.json()["turnos"]
    assert turno["id"] == str(con_cambio)
    assert turno["starts_at_propuesto"] is not None and turno["cambio_pendiente_expira_at"] is not None
    r = await http(app).get("/turnos", params={"cambio_pendiente": "true"}, headers=_h(escenario, "b_owner", False))
    assert [t["id"] for t in r.json()["turnos"]] == [str(ajeno)]
    r = await http(app).get("/turnos", params={"cambio_pendiente": "true"}, headers=_h(escenario, "sin", False))
    assert r.status_code == 403
    # Con rango sigue filtrando por horario y sin cambio_pendiente trae todos los del rango.
    r = await http(app).get(
        "/turnos",
        params={"desde": (BASE - timedelta(days=1)).isoformat(), "hasta": (BASE + timedelta(days=9)).isoformat()},
        headers=_h(escenario, "lectura", False),
    )
    assert len(r.json()["turnos"]) == 2
    r = await http(app).get("/turnos", headers=_h(escenario, "lectura", False))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "rango_requerido"


# --- aislamiento entre negocios ---------------------------------------------------------------------------------


async def test_un_negocio_no_resuelve_los_cambios_de_otro(dashboard, db_superuser, escenario):
    a, b = escenario["a"], escenario["b"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    de_a = await _con_cambio(db_superuser, a)
    de_b = await _con_cambio(db_superuser, b)
    for accion in ("aprobar", "rechazar"):
        r = await http(app).post(f"/turnos/{de_a}/cambio/{accion}", headers=_h(escenario, "b_owner"))
        assert r.status_code == 404 and r.json()["detail"]["codigo"] == "turno_no_encontrado"
        r = await http(app).post(f"/turnos/{de_b}/cambio/{accion}", headers=_h(escenario, "owner"))
        assert r.status_code == 404
    await _sin_efectos(db_superuser, a, de_a)
    await _sin_efectos(db_superuser, b, de_b)
    assert not calendario.llamadas and not bot.mensajes
    # Cada uno sí resuelve el suyo, y solo deja rastro en su negocio.
    assert (await http(app).post(f"/turnos/{de_b}/cambio/aprobar", headers=_h(escenario, "b_owner"))).status_code == 200
    tenant_auditado = await db_superuser.fetchval("select tenant_id from public.audit_log where entity_id = $1", de_b)
    assert tenant_auditado == b["tenant"]
    assert await _auditoria(db_superuser, de_a) == [] and await _avisos(db_superuser, a) == []


# --- concurrencia -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("acciones", [("aprobar", "aprobar"), ("aprobar", "rechazar"), ("rechazar", "rechazar")])
async def test_dos_resoluciones_simultaneas_del_mismo_cambio_dejan_una_sola(
    conectar_como, db_superuser, escenario, acciones
):
    a = escenario["a"]
    pool = _PoolDeVarias([await conectar_como("dashboard_service"), await conectar_como("dashboard_service")])
    app, _, _ = app_con_pool(pool)
    cliente = http(app)
    turno = await _con_cambio(db_superuser, a)
    respuestas = await asyncio.gather(
        *(cliente.post(f"/turnos/{turno}/cambio/{x}", headers=_h(escenario, "owner")) for x in acciones)
    )
    assert sorted(r.status_code for r in respuestas) == [200, 409], [r.text for r in respuestas]
    perdedora = next(r for r in respuestas if r.status_code == 409)
    assert perdedora.json()["detail"]["codigo"] == "cambio_no_pendiente"
    fila = await _fila(db_superuser, turno)
    assert fila["starts_at_propuesto"] is None
    assert fila["inicio"] in (BASE, PROPUESTO)
    assert len(await _auditoria(db_superuser, turno)) == 1
    assert len(await _avisos(db_superuser, a)) == 1
