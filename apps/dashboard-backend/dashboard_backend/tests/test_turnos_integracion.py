"""/turnos y /packs contra Postgres real con el rol `dashboard_service`: estados, exclusion constraint, packs, RLS.

Datos ficticios. Dos tenants para probar el aislamiento. Calendar y Telegram van simulados (sin red).
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_turnos import (  # noqa: E402
    CalendarioSimulado,
    app_con_pool,
)

BASE = datetime.now(timezone.utc).replace(microsecond=0, second=0, minute=0) + timedelta(days=30)  # noqa: UP017
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


async def _tenant(db, sufijo, letra, usuarios):
    """Crea un tenant con su cliente (Telegram), servicio y profesional que lo ofrece. Devuelve sus ids."""
    t = uuid.uuid4()
    await db.execute(
        "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, letra.upper(), f"it-tur-{letra}-{sufijo}"
    )
    cliente, servicio, profesional = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, $2, 'telegram', $3, $4, 'Cliente de prueba')",
        cliente,
        t,
        f"chat-{uuid.uuid4().hex[:8]}",
        f"+54911555{uuid.uuid4().int % 10**7:07d}",
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
        "insert into public.professional_services (tenant_id, professional_id, service_id) values ($1, $2, $3)",
        t,
        profesional,
        servicio,
    )
    return {"tenant": t, "cliente": cliente, "servicio": servicio, "profesional": profesional}


@pytest.fixture
async def escenario(db_superuser):
    """Tenants A y B. En A: owner, solo-lectura, sin permisos y un profesional vinculado sin permisos."""
    sufijo = uuid.uuid4().hex[:8]
    usuarios: dict[str, uuid.UUID] = {}
    a = await _tenant(db_superuser, sufijo, "a", usuarios)
    b = await _tenant(db_superuser, sufijo, "b", usuarios)
    permisos = {
        "owner": (a, None),
        "lectura": (a, ("view_bookings",)),
        "sin": (a, ()),
        "prof": (a, ()),
        "b_owner": (b, None),
    }
    tenant_users = {}
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
        tenant_users[nombre] = tu
    # El profesional de A queda vinculado a la cuenta `prof`.
    await db_superuser.execute(
        "update public.professionals set tenant_user_id = $1 where id = $2", tenant_users["prof"], a["profesional"]
    )
    tenants = [a["tenant"], b["tenant"]]
    datos = {"a": a, "b": b, "usuarios": usuarios}
    try:
        yield datos
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            for tabla in (
                "bookings",
                "booking_packs",
                "notifications_log",
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


async def _turno(db, datos, *, estado="pending", inicio=BASE, cliente=None, evento=None, pack=None, profesional=None):
    """Inserta un turno como superusuario. `pending` lleva vencimiento futuro (lo exige el check de la tabla)."""
    turno = uuid.uuid4()
    await db.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at, google_calendar_event_id, booking_pack_id) "
        "values ($1, $2, $3, $4, $5, tstzrange($6, $7), $8, $9, $10, $11)",
        turno,
        datos["tenant"],
        cliente or datos["cliente"],
        datos["servicio"],
        profesional or datos["profesional"],
        inicio,
        inicio + DURACION,
        estado,
        inicio - timedelta(hours=1) if estado == "pending" else None,
        evento,
        pack,
    )
    return turno


async def _fila(db, turno):
    return await db.fetchrow(
        "select status, pending_expires_at, google_calendar_event_id, lower(time_range) as inicio, "
        "upper(time_range) as fin, booking_pack_id from public.bookings where id = $1",
        turno,
    )


async def _auditoria(db, entidad):
    return [r["action"] for r in await db.fetch("select action from public.audit_log where entity_id = $1", entidad)]


async def _usadas(db, pack):
    return await db.fetchval("select used_sessions from public.booking_packs where id = $1", pack)


def _alta(datos, inicio=BASE, **extra):
    return {
        "end_customer_id": str(datos["cliente"]),
        "service_id": str(datos["servicio"]),
        "professional_id": str(datos["profesional"]),
        "start_at": inicio.isoformat(),
        **extra,
    }


# --- aprobar / rechazar --------------------------------------------------------------------------------------


async def test_aprobar_confirma_audita_guarda_el_evento_y_avisa_con_registro(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, a)
    r = await http(app).post(f"/turnos/{turno}/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    fila = await _fila(db_superuser, turno)
    assert fila["status"] == "confirmed" and fila["pending_expires_at"] is None
    assert fila["google_calendar_event_id"] == "evt-nuevo"
    assert "booking_approved" in await _auditoria(db_superuser, turno)
    assert len(calendario.llamadas) == 1 and calendario.llamadas[0].status == "confirmed"
    assert len(bot.mensajes) == 1
    log = await db_superuser.fetch(
        "select status, template, channel from public.notifications_log where end_customer_id = $1", a["cliente"]
    )
    assert [(x["status"], x["template"], x["channel"]) for x in log] == [("sent", "turno_confirmado", "telegram")]


async def test_rechazar_cancela_sin_tocar_el_calendario(dashboard, db_superuser, escenario):
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"])
    r = await http(app).post(f"/turnos/{turno}/rechazar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert (await _fila(db_superuser, turno))["status"] == "cancelled"
    assert not calendario.llamadas
    assert len(bot.mensajes) == 1


async def test_el_profesional_vinculado_aprueba_su_turno_pero_no_puede_cancelar(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    pendiente = await _turno(db_superuser, a)
    r = await http(app).post(f"/turnos/{pendiente}/aprobar", headers=_h(escenario, "prof"))
    assert r.status_code == 200, r.text
    r = await http(app).post(f"/turnos/{pendiente}/cancelar", headers=_h(escenario, "prof"))
    assert r.status_code == 403
    assert (await _fila(db_superuser, pendiente))["status"] == "confirmed"


async def test_quien_no_es_el_profesional_ni_tiene_el_permiso_no_aprueba(dashboard, db_superuser, escenario):
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"])
    for quien in ("sin", "lectura"):
        r = await http(app).post(f"/turnos/{turno}/aprobar", headers=_h(escenario, quien))
        assert r.status_code == 403
    assert (await _fila(db_superuser, turno))["status"] == "pending"


async def test_aprobar_dos_veces_o_un_turno_cancelado_es_409(dashboard, db_superuser, escenario):
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"])
    assert (await http(app).post(f"/turnos/{turno}/aprobar", headers=_h(escenario, "owner"))).status_code == 200
    r = await http(app).post(f"/turnos/{turno}/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "estado_invalido"
    cancelado = await _turno(db_superuser, escenario["a"], estado="cancelled", inicio=BASE + timedelta(days=1))
    r = await http(app).post(f"/turnos/{cancelado}/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 409


async def test_la_base_rechaza_una_transicion_invalida_aunque_el_backend_la_intentara(
    dashboard, db_superuser, escenario
):
    a = escenario["a"]
    cancelado = await _turno(db_superuser, a, estado="cancelled")
    await dashboard.execute("begin")
    try:
        await dashboard.execute("select set_config('app.current_tenant_id', $1, true)", str(a["tenant"]))
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await dashboard.execute("update public.bookings set status = 'confirmed' where id = $1", cancelado)
    finally:
        await dashboard.execute("rollback")
    assert (await _fila(db_superuser, cancelado))["status"] == "cancelled"


# --- mover / cancelar / completar ----------------------------------------------------------------------------


async def test_mover_actualiza_el_mismo_turno_y_un_choque_es_409_sin_cambios(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    primero = await _turno(db_superuser, a, estado="confirmed", inicio=BASE, evento="evt-1")
    segundo = await _turno(db_superuser, a, estado="confirmed", inicio=BASE + timedelta(hours=2), evento="evt-2")
    # Choque con el primero: el exclusion constraint lo rechaza.
    r = await http(app).post(
        f"/turnos/{segundo}/mover",
        json={"start_at": (BASE + timedelta(minutes=15)).isoformat()},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["codigo"] == "horario_ocupado"
    assert (await _fila(db_superuser, segundo))["inicio"] == BASE + timedelta(hours=2)
    assert not calendario.llamadas and not bot.mensajes
    # Horario libre: mismo booking, mismo id, misma duración.
    nuevo = BASE + timedelta(hours=5)
    r = await http(app).post(
        f"/turnos/{segundo}/mover", json={"start_at": nuevo.isoformat()}, headers=_h(escenario, "owner")
    )
    assert r.status_code == 200, r.text
    fila = await _fila(db_superuser, segundo)
    assert (fila["inicio"], fila["fin"]) == (nuevo, nuevo + DURACION)
    assert await db_superuser.fetchval("select count(*) from public.bookings where tenant_id = $1", a["tenant"]) == 2
    assert (await _fila(db_superuser, primero))["inicio"] == BASE
    assert calendario.llamadas[0].google_calendar_event_id == "evt-2" and calendario.llamadas[0].inicio == nuevo
    assert "booking_moved" in await _auditoria(db_superuser, segundo)
    assert len(bot.mensajes) == 1


async def test_cancelar_borra_el_evento_avisa_y_no_deja_de_existir_la_fila(dashboard, db_superuser, escenario):
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"], estado="confirmed", evento="evt-9")
    r = await http(app).post(f"/turnos/{turno}/cancelar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    fila = await _fila(db_superuser, turno)
    assert fila["status"] == "cancelled" and fila["google_calendar_event_id"] is None
    assert calendario.llamadas[0].status == "cancelled" and calendario.llamadas[0].google_calendar_event_id == "evt-9"
    assert len(bot.mensajes) == 1
    r = await http(app).post(f"/turnos/{turno}/cancelar", headers=_h(escenario, "owner"))
    assert r.status_code == 409


async def test_completar_un_confirmado_y_no_se_puede_volver_atras(dashboard, db_superuser, escenario):
    app, calendario, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"], estado="confirmed")
    assert (await http(app).post(f"/turnos/{turno}/completar", headers=_h(escenario, "owner"))).status_code == 200
    assert (await _fila(db_superuser, turno))["status"] == "completed"
    assert not calendario.llamadas
    r = await http(app).post(f"/turnos/{turno}/cancelar", headers=_h(escenario, "owner"))
    assert r.status_code == 409
    assert (await _fila(db_superuser, turno))["status"] == "completed"


# --- packs ---------------------------------------------------------------------------------------------------


async def test_cancelar_devuelve_exactamente_una_sesion_del_pack(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    r = await http(app).post(
        "/packs",
        json={"end_customer_id": str(a["cliente"]), "service_id": str(a["servicio"]), "total_sessions": 3},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 201, r.text
    pack = uuid.UUID(r.json()["id"])
    assert r.json()["origin"] == "manual_grant" and r.json()["payment_status"] == "not_required"
    turnos = []
    for i in range(2):
        r = await http(app).post(
            "/turnos",
            json=_alta(a, BASE + timedelta(hours=i), booking_pack_id=str(pack)),
            headers=_h(escenario, "owner"),
        )
        assert r.status_code == 201, r.text
        turnos.append(r.json()["id"])
    assert await _usadas(db_superuser, pack) == 2
    r = await http(app).post(f"/turnos/{turnos[0]}/cancelar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert await _usadas(db_superuser, pack) == 1  # bajó exactamente 1, por el trigger
    r = await http(app).post(f"/turnos/{turnos[0]}/cancelar", headers=_h(escenario, "owner"))
    assert r.status_code == 409
    assert await _usadas(db_superuser, pack) == 1  # un segundo cancelar no devuelve otra sesión
    listado = await http(app).get(
        f"/packs?end_customer_id={a['cliente']}", headers=_h(escenario, "lectura", escribe=False)
    )
    assert [(p["id"], p["used_sessions"]) for p in listado.json()["packs"]] == [(str(pack), 1)]


async def test_un_pack_se_agota_y_el_sobreuso_es_409(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    pack = await db_superuser.fetchval(
        "insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin) "
        "values ($1, $2, $3, 1, 'manual_grant') returning id",
        a["tenant"],
        a["cliente"],
        a["servicio"],
    )
    ok = await http(app).post("/turnos", json=_alta(a, booking_pack_id=str(pack)), headers=_h(escenario, "owner"))
    assert ok.status_code == 201, ok.text
    r = await http(app).post(
        "/turnos", json=_alta(a, BASE + timedelta(hours=3), booking_pack_id=str(pack)), headers=_h(escenario, "owner")
    )
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "pack_no_disponible"
    assert await _usadas(db_superuser, pack) == 1


async def test_un_pack_comprado_sin_pagar_no_se_redime_y_no_consume_sesion(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    pack = await db_superuser.fetchval(
        "insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, "
        "payment_status) values ($1, $2, $3, 5, 'purchase', 'pending') returning id",
        a["tenant"],
        a["cliente"],
        a["servicio"],
    )
    r = await http(app).post("/turnos", json=_alta(a, booking_pack_id=str(pack)), headers=_h(escenario, "owner"))
    assert r.status_code == 422, r.text
    assert await _usadas(db_superuser, pack) == 0  # el rollback deshizo el consumo
    assert await db_superuser.fetchval("select count(*) from public.bookings where tenant_id = $1", a["tenant"]) == 0
    assert not calendario.llamadas


async def test_un_pack_de_otro_cliente_no_se_puede_usar(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    otro = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, phone, name) values ($1, '+5491155509999', 'Otro') returning id",
        a["tenant"],
    )
    pack = await db_superuser.fetchval(
        "insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin) "
        "values ($1, $2, $3, 3, 'manual_grant') returning id",
        a["tenant"],
        otro,
        a["servicio"],
    )
    r = await http(app).post("/turnos", json=_alta(a, booking_pack_id=str(pack)), headers=_h(escenario, "owner"))
    assert r.status_code == 409
    assert await _usadas(db_superuser, pack) == 0


# --- alta del negocio y doble reserva ------------------------------------------------------------------------


async def test_crear_nace_confirmado_con_evento_y_el_choque_es_409(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    r = await http(app).post("/turnos", json=_alta(a, tenant_id=str(uuid.uuid4())), headers=_h(escenario, "owner"))
    assert r.status_code == 201, r.text
    fila = await _fila(db_superuser, uuid.UUID(r.json()["id"]))
    assert fila["status"] == "confirmed" and fila["fin"] - fila["inicio"] == DURACION
    assert fila["google_calendar_event_id"] == "evt-nuevo"
    assert not bot.mensajes
    choque = await http(app).post(
        "/turnos", json=_alta(a, BASE + timedelta(minutes=10)), headers=_h(escenario, "owner")
    )
    assert choque.status_code == 409 and choque.json()["detail"]["codigo"] == "horario_ocupado"
    assert len(calendario.llamadas) == 1
    sin_permiso = await http(app).post(
        "/turnos", json=_alta(a, BASE + timedelta(days=1)), headers=_h(escenario, "lectura")
    )
    assert sin_permiso.status_code == 403


async def test_dos_altas_simultaneas_del_mismo_horario_dejan_un_solo_turno(conectar_como, db_superuser, escenario):
    a = escenario["a"]
    pool = _PoolDeVarias([await conectar_como("dashboard_service"), await conectar_como("dashboard_service")])
    app, _, _ = app_con_pool(pool)
    cliente = http(app)
    h = _h(escenario, "owner")
    # El choque de dos inserts en el exclusion constraint deadlockea de forma intermitente: se repite la carrera
    # varias veces (un horario distinto por ronda, uno de ellos solapado y no idéntico) para no depender de la suerte.
    for ronda in range(10):
        inicio = BASE + timedelta(days=1 + ronda)
        cuerpos = [_alta(a, inicio), _alta(a, inicio + timedelta(minutes=10 * (ronda % 2)))]
        respuestas = await asyncio.gather(*(cliente.post("/turnos", json=c, headers=h) for c in cuerpos))
        assert sorted(r.status_code for r in respuestas) == [201, 409], (ronda, [r.text for r in respuestas])
        assert all("deadlock" not in r.text for r in respuestas)
        vigentes = await db_superuser.fetchval(
            "select count(*) from public.bookings where professional_id = $1 and status <> 'cancelled' "
            "and time_range && tstzrange($2, $3)",
            a["profesional"],
            inicio,
            inicio + timedelta(minutes=40),
        )
        assert vigentes == 1, ronda


# --- Calendar que falla --------------------------------------------------------------------------------------


async def test_un_fallo_de_calendar_no_revierte_el_cambio_y_queda_registrado(dashboard, db_superuser, escenario):
    app, calendario, bot = app_con_pool(
        _PoolDeUnaConexion(dashboard), calendario=CalendarioSimulado(error=RuntimeError("google caido"))
    )
    turno = await _turno(db_superuser, escenario["a"])
    r = await http(app).post(f"/turnos/{turno}/aprobar", headers=_h(escenario, "owner"))
    assert r.status_code == 200, r.text
    assert r.json()["calendario"]["estado"] == "error"
    fila = await _fila(db_superuser, turno)
    assert fila["status"] == "confirmed" and fila["google_calendar_event_id"] is None
    acciones = await _auditoria(db_superuser, turno)
    assert "booking_approved" in acciones and "calendar_sync_failed" in acciones
    assert len(bot.mensajes) == 1


# --- clientes anonimizados -----------------------------------------------------------------------------------


async def test_los_turnos_de_un_cliente_anonimizado_se_marcan_y_no_se_le_avisa(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    anonimo = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_hash, deleted_at) "
        "values ($1, 'telegram', 'hash-de-prueba', now()) returning id",
        a["tenant"],
    )
    turno = await _turno(db_superuser, a, estado="confirmed", cliente=anonimo, evento="evt-5")
    normal = await _turno(db_superuser, a, estado="confirmed", inicio=BASE + timedelta(hours=3))
    desde, hasta = (BASE - timedelta(days=1)).isoformat(), (BASE + timedelta(days=1)).isoformat()
    r = await http(app).get(
        "/turnos", params={"desde": desde, "hasta": hasta}, headers=_h(escenario, "lectura", escribe=False)
    )
    assert r.status_code == 200, r.text
    marcas = {x["id"]: x["sin_cliente_identificable"] for x in r.json()["turnos"]}
    assert marcas == {str(turno): True, str(normal): False}
    detalle = await http(app).get(f"/turnos/{turno}", headers=_h(escenario, "lectura", escribe=False))
    assert detalle.json()["sin_cliente_identificable"] is True
    r = await http(app).post(
        f"/turnos/{turno}/mover",
        json={"start_at": (BASE + timedelta(hours=8)).isoformat()},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "cliente_anonimizado" and not bot.mensajes
    assert calendario.llamadas[0].cliente_telefono == ""
    r = await http(app).post(
        "/packs",
        json={"end_customer_id": str(anonimo), "service_id": str(a["servicio"]), "total_sessions": 2},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 409


# --- permisos de lectura y aislamiento entre tenants ---------------------------------------------------------


async def test_la_lectura_exige_view_bookings(dashboard, db_superuser, escenario):
    app, _, _ = app_con_pool(_PoolDeUnaConexion(dashboard))
    turno = await _turno(db_superuser, escenario["a"])
    desde, hasta = (BASE - timedelta(days=1)).isoformat(), (BASE + timedelta(days=1)).isoformat()
    h = _h(escenario, "sin", escribe=False)
    assert (await http(app).get("/turnos", params={"desde": desde, "hasta": hasta}, headers=h)).status_code == 403
    assert (await http(app).get(f"/turnos/{turno}", headers=h)).status_code == 403
    assert (await http(app).get("/packs", headers=h)).status_code == 403
    ok = await http(app).get(f"/turnos/{turno}", headers=_h(escenario, "lectura", escribe=False))
    assert ok.status_code == 200 and ok.json()["status"] == "pending" and "tenant_id" not in ok.json()


async def test_un_tenant_no_ve_ni_toca_los_turnos_y_packs_de_otro(dashboard, db_superuser, escenario):
    a = escenario["a"]
    app, calendario, bot = app_con_pool(_PoolDeUnaConexion(dashboard))
    pendiente = await _turno(db_superuser, a)
    confirmado = await _turno(db_superuser, a, estado="confirmed", inicio=BASE + timedelta(hours=3), evento="evt-1")
    pack = await db_superuser.fetchval(
        "insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin) "
        "values ($1, $2, $3, 3, 'manual_grant') returning id",
        a["tenant"],
        a["cliente"],
        a["servicio"],
    )
    h = _h(escenario, "b_owner")
    hl = _h(escenario, "b_owner", escribe=False)
    desde, hasta = (BASE - timedelta(days=1)).isoformat(), (BASE + timedelta(days=1)).isoformat()
    assert (await http(app).get("/turnos", params={"desde": desde, "hasta": hasta}, headers=hl)).json()["turnos"] == []
    assert (await http(app).get(f"/turnos/{pendiente}", headers=hl)).status_code == 404
    assert (await http(app).get("/packs", headers=hl)).json()["packs"] == []
    for accion in ("aprobar", "rechazar"):
        assert (await http(app).post(f"/turnos/{pendiente}/{accion}", headers=h)).status_code == 404
    for accion in ("cancelar", "completar"):
        assert (await http(app).post(f"/turnos/{confirmado}/{accion}", headers=h)).status_code == 404
    r = await http(app).post(f"/turnos/{confirmado}/mover", json={"start_at": BASE.isoformat()}, headers=h)
    assert r.status_code == 404
    # Crear usando ids de A desde B: no existen en B.
    assert (await http(app).post("/turnos", json=_alta(a), headers=h)).status_code == 422
    r = await http(app).post(
        "/packs",
        json={"end_customer_id": str(a["cliente"]), "service_id": str(a["servicio"]), "total_sessions": 2},
        headers=h,
    )
    assert r.status_code == 404
    assert (await _fila(db_superuser, pendiente))["status"] == "pending"
    assert (await _fila(db_superuser, confirmado))["status"] == "confirmed"
    assert await _usadas(db_superuser, pack) == 0
    assert not calendario.llamadas and not bot.mensajes
    assert (
        await db_superuser.fetchval("select count(*) from public.booking_packs where tenant_id = $1", a["tenant"]) == 1
    )
