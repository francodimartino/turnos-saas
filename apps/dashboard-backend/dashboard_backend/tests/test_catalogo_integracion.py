"""Catálogo, disponibilidad y settings contra Postgres real como `dashboard_service` (RLS, grants, checks y
triggers de la base), con dos tenants para probar el aislamiento en cada recurso."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http, token  # noqa: E402


class _PoolDeUnaConexion:
    """Adapta la conexión de `conectar_como("dashboard_service")` a la interfaz de pool (acquire)."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def _borrar(conn, sql, *args):
    """DELETE de limpieza sin triggers ni FKs (las invariantes de último owner las frenarían)."""
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Dos tenants activos. En cada uno: un owner; en A además un staff sin permisos, uno con `edit_prices`, uno
    con `manage_staff` y uno con `manage_schedule`."""
    sufijo = uuid.uuid4().hex[:8]
    tenants = {"a": uuid.uuid4(), "b": uuid.uuid4()}
    for n, t in tenants.items():
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n.upper(), f"it-cat-{n}-{sufijo}"
        )
    usuarios = {  # nombre -> (auth_user_id, tenant, is_owner, permiso)
        "owner_a": (uuid.uuid4(), "a", True, None),
        "owner_b": (uuid.uuid4(), "b", True, None),
        "sin": (uuid.uuid4(), "a", False, None),
        "precios": (uuid.uuid4(), "a", False, "edit_prices"),
        "personal": (uuid.uuid4(), "a", False, "manage_staff"),
        "agenda": (uuid.uuid4(), "a", False, "manage_schedule"),
    }
    tu = {}  # nombre -> tenant_users.id
    for nombre, (auth_id, t, es_owner, permiso) in usuarios.items():
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", auth_id, f"{auth_id.hex[:12]}@prueba.invalid"
        )
        tu[nombre] = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            tenants[t],
            auth_id,
            es_owner,
        )
        if permiso:
            await db_superuser.execute(
                "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, $2)",
                tu[nombre],
                permiso,
            )
    try:
        yield {
            "tenant": tenants,
            "usuario": {n: u[0] for n, u in usuarios.items()},
            "tenant_user": tu,
            "de": {n: tenants[u[1]] for n, u in usuarios.items()},
        }
    finally:
        ids = list(tenants.values())
        for tabla in (
            "availability_blocks",
            "availability_rules",
            "professional_services",
            "tenant_integrations",
            "professionals",
            "services",
            "tenant_user_permissions",
            "audit_log",
            "tenant_users",
        ):
            await _borrar(db_superuser, f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", ids)
        await _borrar(
            db_superuser, "delete from auth.users where id = any($1::uuid[])", [u[0] for u in usuarios.values()]
        )


@pytest.fixture
async def app(conectar_como):
    conn = await conectar_como("dashboard_service")
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    return app


class Cliente:
    """Un usuario del escenario llamando al dashboard-backend (cookie de sesión aal2, origen válido)."""

    def __init__(self, app, escenario, nombre, owner=False):
        self._http = http(app)
        self._headers = {
            "Origin": ORIGEN,
            "Cookie": "dash_acceso="
            + token(
                user_id=escenario["usuario"][nombre],
                tenant_id=escenario["de"][nombre],
                is_owner=owner,
                aal="aal2" if owner else "aal1",
            ),
        }

    async def __call__(self, metodo, ruta, cuerpo=None, **params):
        return await self._http.request(metodo, ruta, json=cuerpo, params=params or None, headers=self._headers)


@pytest.fixture
def a(app, escenario):
    return Cliente(app, escenario, "owner_a", owner=True)


@pytest.fixture
def b(app, escenario):
    return Cliente(app, escenario, "owner_b", owner=True)


SERVICIO = {"name": "Corte", "duration_minutes": 30, "price": 15000}


async def _servicio(cliente, **cambios):
    r = await cliente("POST", "/servicios", {**SERVICIO, **cambios})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _profesional(cliente, **cuerpo):
    r = await cliente("POST", "/profesionales", {"name": "Ana", **cuerpo})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- servicios --------------------------------------------------------------------------------------------------


async def test_crud_de_servicios_y_el_tenant_sale_de_la_sesion(a, escenario, db_superuser):
    otro = escenario["tenant"]["b"]
    r = await a("POST", "/servicios", {**SERVICIO, "tenant_id": str(otro)})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert (
        await db_superuser.fetchval("select tenant_id from public.services where id = $1", uuid.UUID(sid))
        == (escenario["tenant"]["a"])
    )
    assert (await a("GET", f"/servicios/{sid}")).json()["price"] == 15000
    r = await a("PATCH", f"/servicios/{sid}", {"price": 18000.5, "deposit_amount": 2000})
    assert r.status_code == 200 and r.json()["price"] == 18000.5
    assert [s["id"] for s in (await a("GET", "/servicios")).json()] == [sid]
    assert (await a("DELETE", f"/servicios/{sid}")).status_code == 204
    assert (await a("GET", f"/servicios/{sid}")).status_code == 404


async def test_servicios_exigen_edit_prices_para_escribir(app, escenario):
    sin = Cliente(app, escenario, "sin")
    precios = Cliente(app, escenario, "precios")
    personal = Cliente(app, escenario, "personal")
    assert (await sin("GET", "/servicios")).status_code == 200  # leer: cualquier staff
    assert (await sin("POST", "/servicios", SERVICIO)).status_code == 403
    assert (await personal("POST", "/servicios", SERVICIO)).status_code == 403  # manage_staff no alcanza
    r = await precios("POST", "/servicios", SERVICIO)
    assert r.status_code == 201
    assert (await sin("PATCH", f"/servicios/{r.json()['id']}", {"price": 1})).status_code == 403
    assert (await sin("DELETE", f"/servicios/{r.json()['id']}")).status_code == 403


async def test_checks_de_servicios_son_422_con_mensaje(a):
    r = await a("POST", "/servicios", {**SERVICIO, "duration_minutes": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "valor_invalido" and "duración" in r.json()["detail"]["mensaje"]
    r = await a("POST", "/servicios", {**SERVICIO, "price": -1})
    assert r.status_code == 422 and "precio" in r.json()["detail"]["mensaje"]
    sid = await _servicio(a)
    assert (await a("PATCH", f"/servicios/{sid}", {"duration_minutes": -5})).status_code == 422
    assert (await a("GET", f"/servicios/{sid}")).json()["duration_minutes"] == 30  # el rechazo no dejó rastro


async def test_servicio_usado_por_un_turno_no_se_borra_pero_si_se_desactiva(a, escenario, db_superuser):
    sid = await _servicio(a)
    pid = await _profesional(a)
    cliente = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id) values ($1, 'telegram', $2) "
        "returning id",
        escenario["tenant"]["a"],
        f"it-{uuid.uuid4().hex[:8]}",
    )
    await db_superuser.execute(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, "
        "pending_expires_at) values ($1, $2, $3, $4, tstzrange(now() + interval '1 day', "
        "now() + interval '1 day 30 minutes'), 'confirmed', null)",
        escenario["tenant"]["a"],
        cliente,
        uuid.UUID(sid),
        uuid.UUID(pid),
    )
    try:
        r = await a("DELETE", f"/servicios/{sid}")
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "en_uso"
        assert (await a("PATCH", f"/servicios/{sid}", {"is_active": False})).json()["is_active"] is False
    finally:
        await _borrar(db_superuser, "delete from public.bookings where tenant_id = $1", escenario["tenant"]["a"])
        await _borrar(db_superuser, "delete from public.end_customers where id = $1", cliente)


async def test_aislamiento_de_servicios(a, b):
    sid = await _servicio(a)
    assert (await b("GET", f"/servicios/{sid}")).status_code == 404
    assert (await b("PATCH", f"/servicios/{sid}", {"price": 1})).status_code == 404
    assert (await b("DELETE", f"/servicios/{sid}")).status_code == 404
    assert (await b("GET", "/servicios")).json() == []
    assert (await a("GET", f"/servicios/{sid}")).json()["price"] == 15000  # intacto


# --- profesionales ----------------------------------------------------------------------------------------------


async def test_crud_de_profesionales_con_vinculo_opcional_a_una_cuenta(a, escenario):
    suelto = await _profesional(a)
    uid = str(escenario["tenant_user"]["personal"])
    r = await a("PATCH", f"/profesionales/{suelto}", {"tenant_user_id": uid})
    assert r.status_code == 200 and r.json()["tenant_user_id"] == uid
    r = await a("PATCH", f"/profesionales/{suelto}", {"tenant_user_id": None})  # null explícito desvincula
    assert r.json()["tenant_user_id"] is None
    assert (await a("GET", f"/profesionales/{suelto}")).json()["name"] == "Ana"
    assert (await a("DELETE", f"/profesionales/{suelto}")).status_code == 204
    assert (await a("GET", f"/profesionales/{suelto}")).status_code == 404


async def test_vinculo_a_una_cuenta_de_otro_tenant_o_ya_usada(a, escenario):
    ajeno = str(escenario["tenant_user"]["owner_b"])
    r = await a("POST", "/profesionales", {"name": "X", "tenant_user_id": ajeno})
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "referencia_invalida"
    propio = str(escenario["tenant_user"]["personal"])
    await _profesional(a, tenant_user_id=propio)
    r = await a("POST", "/profesionales", {"name": "Y", "tenant_user_id": propio})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "duplicado"


async def test_profesionales_exigen_manage_staff_para_escribir(app, escenario):
    personal = Cliente(app, escenario, "personal")
    precios = Cliente(app, escenario, "precios")
    assert (await precios("GET", "/profesionales")).status_code == 200
    assert (await precios("POST", "/profesionales", {"name": "Z"})).status_code == 403
    assert (await personal("POST", "/profesionales", {"name": "Z"})).status_code == 201


async def test_desactivar_un_profesional_elimina_su_secret_de_calendar(a, escenario, db_superuser):
    pid = await _profesional(a)
    t = escenario["tenant"]["a"]
    nombre = f"it-cal-{uuid.uuid4().hex[:8]}"
    secret_id = await db_superuser.fetchval("select vault.create_secret('token-de-prueba', $1)", nombre)
    await db_superuser.execute(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id, secret_id) "
        "values ($1, 'google_calendar', $2, $3)",
        t,
        uuid.UUID(pid),
        secret_id,
    )
    assert await db_superuser.fetchval("select count(*) from vault.secrets where id = $1", secret_id) == 1
    try:
        r = await a("PATCH", f"/profesionales/{pid}", {"name": "Ana B"})  # renombrar no lo toca
        assert r.status_code == 200
        assert await db_superuser.fetchval("select count(*) from vault.secrets where id = $1", secret_id) == 1
        r = await a("PATCH", f"/profesionales/{pid}", {"is_active": False})
        assert r.status_code == 200 and r.json()["is_active"] is False
        assert await db_superuser.fetchval("select count(*) from vault.secrets where id = $1", secret_id) == 0
        assert (
            await db_superuser.fetchval(
                "select secret_id from public.tenant_integrations where professional_id = $1", uuid.UUID(pid)
            )
            is None
        )
    finally:
        await db_superuser.execute("delete from vault.secrets where id = $1", secret_id)


async def test_aislamiento_de_profesionales(a, b):
    pid = await _profesional(a)
    assert (await b("GET", f"/profesionales/{pid}")).status_code == 404
    assert (await b("PATCH", f"/profesionales/{pid}", {"is_active": False})).status_code == 404
    assert (await b("DELETE", f"/profesionales/{pid}")).status_code == 404
    assert (await b("GET", "/profesionales")).json() == []
    assert (await a("GET", f"/profesionales/{pid}")).json()["is_active"] is True


# --- professional_services --------------------------------------------------------------------------------------


async def test_servicios_de_un_profesional_auto_approval_y_deposit_rechazado(a):
    pid, sid = await _profesional(a), await _servicio(a, deposit_amount=1000)
    ruta = f"/profesionales/{pid}/servicios/{sid}"
    r = await a("PUT", ruta, {"confirmation_mode": "auto"})
    assert r.status_code == 200 and r.json()["confirmation_mode"] == "auto"
    r = await a("PUT", ruta, {"confirmation_mode": "approval"})  # upsert: cambia el modo
    assert r.status_code == 200 and r.json()["confirmation_mode"] == "approval"
    r = await a("PUT", ruta, {"confirmation_mode": "deposit"})  # aunque el servicio tenga seña
    assert r.status_code == 422
    listado = (await a("GET", f"/profesionales/{pid}/servicios")).json()
    assert [(x["service_id"], x["confirmation_mode"]) for x in listado] == [(sid, "approval")]
    assert (await a("DELETE", ruta)).status_code == 204
    assert (await a("DELETE", ruta)).status_code == 404
    assert (await a("GET", f"/profesionales/{pid}/servicios")).json() == []


async def test_aislamiento_de_professional_services(a, b):
    pid, sid = await _profesional(a), await _servicio(a)
    await a("PUT", f"/profesionales/{pid}/servicios/{sid}", {"confirmation_mode": "auto"})
    pb, sb = await _profesional(b), await _servicio(b)
    # B no puede ver ni tocar los vínculos de A, ni cruzar su profesional con un servicio de A (o al revés)
    assert (await b("GET", f"/profesionales/{pid}/servicios")).status_code == 404
    assert (await b("PUT", f"/profesionales/{pid}/servicios/{sid}", {"confirmation_mode": "auto"})).status_code == 404
    assert (await b("DELETE", f"/profesionales/{pid}/servicios/{sid}")).status_code == 404
    assert (await b("PUT", f"/profesionales/{pb}/servicios/{sid}", {"confirmation_mode": "auto"})).status_code == 404
    assert (await b("PUT", f"/profesionales/{pid}/servicios/{sb}", {"confirmation_mode": "auto"})).status_code == 404
    assert len((await a("GET", f"/profesionales/{pid}/servicios")).json()) == 1


# --- availability_rules -----------------------------------------------------------------------------------------


def _regla(pid, **cambios):
    return {"professional_id": pid, "day_of_week": 1, "start_time": "09:00", "end_time": "13:00", **cambios}


async def test_crud_de_reglas_de_disponibilidad(a):
    pid = await _profesional(a)
    r = await a("POST", "/reglas-disponibilidad", _regla(pid))
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["start_time"] == "09:00:00"
    r = await a("PATCH", f"/reglas-disponibilidad/{rid}", {"end_time": "20:00", "day_of_week": 5})
    assert r.status_code == 200 and r.json()["end_time"] == "20:00:00" and r.json()["day_of_week"] == 5
    assert [x["id"] for x in (await a("GET", "/reglas-disponibilidad", professional_id=pid)).json()] == [rid]
    assert (await a("DELETE", f"/reglas-disponibilidad/{rid}")).status_code == 204
    assert (await a("GET", f"/reglas-disponibilidad/{rid}")).status_code == 404


async def test_ventana_invertida_o_dia_invalido_son_422_con_mensaje(a):
    pid = await _profesional(a)
    r = await a("POST", "/reglas-disponibilidad", _regla(pid, start_time="18:00", end_time="09:00"))
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "valor_invalido"
    assert "fin" in r.json()["detail"]["mensaje"] and "inicio" in r.json()["detail"]["mensaje"]
    r = await a("POST", "/reglas-disponibilidad", _regla(pid, start_time="09:00", end_time="09:00"))
    assert r.status_code == 422
    assert (await a("POST", "/reglas-disponibilidad", _regla(pid, day_of_week=7))).status_code == 422
    ok = (await a("POST", "/reglas-disponibilidad", _regla(pid))).json()["id"]
    r = await a("PATCH", f"/reglas-disponibilidad/{ok}", {"end_time": "08:00"})  # invierte una regla existente
    assert r.status_code == 422


async def test_regla_para_un_profesional_inexistente_o_ajeno_es_422(a, b):
    pb = await _profesional(b)
    r = await a("POST", "/reglas-disponibilidad", _regla(pb))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "referencia_invalida"
    assert (await a("POST", "/reglas-disponibilidad", _regla(str(uuid.uuid4())))).status_code == 422


async def test_aislamiento_de_reglas(a, b):
    pid = await _profesional(a)
    rid = (await a("POST", "/reglas-disponibilidad", _regla(pid))).json()["id"]
    assert (await b("GET", f"/reglas-disponibilidad/{rid}")).status_code == 404
    assert (await b("PATCH", f"/reglas-disponibilidad/{rid}", {"day_of_week": 2})).status_code == 404
    assert (await b("DELETE", f"/reglas-disponibilidad/{rid}")).status_code == 404
    assert (await b("GET", "/reglas-disponibilidad")).json() == []
    assert (await b("GET", "/reglas-disponibilidad", professional_id=pid)).json() == []
    assert (await a("GET", f"/reglas-disponibilidad/{rid}")).json()["day_of_week"] == 1


# --- availability_blocks ----------------------------------------------------------------------------------------


def _bloqueo(**cambios):
    return {
        "start_at": "2026-12-24T00:00:00-03:00",
        "end_at": "2026-12-26T00:00:00-03:00",
        "reason": "Feriado",
        **cambios,
    }


async def test_crud_de_bloqueos_del_local_y_de_un_profesional(a):
    pid = await _profesional(a)
    del_local = (await a("POST", "/bloqueos-disponibilidad", _bloqueo())).json()
    assert del_local["professional_id"] is None and del_local["reason"] == "Feriado"
    r = await a("POST", "/bloqueos-disponibilidad", _bloqueo(professional_id=pid, reason="Vacaciones"))
    assert r.status_code == 201, r.text
    del_prof = r.json()
    assert del_prof["professional_id"] == pid
    assert del_prof["start_at"].startswith("2026-12-24") or del_prof["start_at"].startswith("2026-12-23")
    assert len((await a("GET", "/bloqueos-disponibilidad")).json()) == 2
    assert [x["id"] for x in (await a("GET", "/bloqueos-disponibilidad", professional_id=pid)).json()] == [
        del_prof["id"]
    ]
    r = await a(
        "PATCH",
        f"/bloqueos-disponibilidad/{del_prof['id']}",
        {"reason": "Licencia", "end_at": "2026-12-30T00:00:00-03:00"},
    )
    assert r.status_code == 200 and r.json()["reason"] == "Licencia"
    assert (await a("DELETE", f"/bloqueos-disponibilidad/{del_local['id']}")).status_code == 204
    assert (await a("GET", f"/bloqueos-disponibilidad/{del_local['id']}")).status_code == 404


async def test_bloqueo_vacio_o_invertido_es_422(a):
    r = await a("POST", "/bloqueos-disponibilidad", _bloqueo(end_at="2026-12-24T00:00:00-03:00"))  # rango vacío
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "valor_invalido"
    assert (
        await a("POST", "/bloqueos-disponibilidad", _bloqueo(end_at="2026-12-01T00:00:00-03:00"))
    ).status_code == 422
    bid = (await a("POST", "/bloqueos-disponibilidad", _bloqueo())).json()["id"]
    assert (
        await a("PATCH", f"/bloqueos-disponibilidad/{bid}", {"end_at": "2026-12-20T00:00:00-03:00"})
    ).status_code == 422


async def test_bloqueo_de_un_profesional_ajeno_es_422(a, b):
    pb = await _profesional(b)
    r = await a("POST", "/bloqueos-disponibilidad", _bloqueo(professional_id=pb))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "referencia_invalida"


async def test_aislamiento_de_bloqueos(a, b):
    bid = (await a("POST", "/bloqueos-disponibilidad", _bloqueo())).json()["id"]  # bloqueo de todo el local de A
    assert (await b("GET", f"/bloqueos-disponibilidad/{bid}")).status_code == 404
    assert (await b("PATCH", f"/bloqueos-disponibilidad/{bid}", {"reason": "hack"})).status_code == 404
    assert (await b("DELETE", f"/bloqueos-disponibilidad/{bid}")).status_code == 404
    assert (await b("GET", "/bloqueos-disponibilidad")).json() == []
    assert (await a("GET", f"/bloqueos-disponibilidad/{bid}")).json()["reason"] == "Feriado"


async def test_reglas_y_bloqueos_exigen_manage_schedule_para_escribir(app, escenario):
    owner = Cliente(app, escenario, "owner_a", owner=True)
    pid = await _profesional(owner)
    precios = Cliente(app, escenario, "precios")
    personal = Cliente(app, escenario, "personal")
    agenda = Cliente(app, escenario, "agenda")
    assert (await precios("POST", "/reglas-disponibilidad", _regla(pid))).status_code == 403
    assert (await precios("POST", "/bloqueos-disponibilidad", _bloqueo())).status_code == 403
    assert (await precios("GET", "/bloqueos-disponibilidad")).status_code == 200
    # manage_staff (el permiso viejo) ya no alcanza.
    assert (await personal("POST", "/reglas-disponibilidad", _regla(pid))).status_code == 403
    assert (await personal("POST", "/bloqueos-disponibilidad", _bloqueo())).status_code == 403
    assert (await agenda("POST", "/reglas-disponibilidad", _regla(pid))).status_code == 201
    assert (await agenda("POST", "/bloqueos-disponibilidad", _bloqueo())).status_code == 201
    assert (await owner("POST", "/bloqueos-disponibilidad", _bloqueo())).status_code == 201


# --- tenants.settings -------------------------------------------------------------------------------------------


async def test_settings_se_leen_y_se_mezclan_sin_pisar_lo_otro(a, escenario, db_superuser):
    assert (await a("GET", "/local/settings")).json() == {"settings": {}}
    r = await a(
        "PATCH",
        "/local/settings",
        {"settings": {"direccion": "Calle Falsa 123", "booking_approval_timeout_hours": 48, "campos": ["dni"]}},
    )
    assert r.status_code == 200, r.text
    r = await a("PATCH", "/local/settings", {"settings": {"telefono": "+54 11 5555-0000", "direccion": None}})
    assert r.json()["settings"] == {
        "telefono": "+54 11 5555-0000",
        "booking_approval_timeout_hours": 48,
        "campos": ["dni"],
    }
    assert (await a("GET", "/local/settings")).json() == r.json()
    guardado = await db_superuser.fetchval(
        "select settings::text from public.tenants where id = $1", escenario["tenant"]["a"]
    )
    assert "Calle Falsa" not in guardado and "48" in guardado


@pytest.mark.parametrize("horas", [0, 169, -1, 1000])
async def test_horas_de_aprobacion_fuera_de_1_a_168_son_422_y_no_se_guardan(a, horas):
    assert (
        await a("PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": 24}})
    ).status_code == 200
    r = await a("PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": horas}})
    assert r.status_code == 422, r.text
    assert "1 y 168" in r.json()["detail"]["mensaje"]
    assert (await a("GET", "/local/settings")).json()["settings"]["booking_approval_timeout_hours"] == 24


@pytest.mark.parametrize("horas", [1, 168])
async def test_los_extremos_1_y_168_son_validos(a, horas):
    r = await a("PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": horas}})
    assert r.status_code == 200 and r.json()["settings"]["booking_approval_timeout_hours"] == horas


async def test_settings_exigen_manage_schedule_para_escribir_y_leer_cualquier_staff(app, escenario):
    sin = Cliente(app, escenario, "sin")
    personal = Cliente(app, escenario, "personal")
    agenda = Cliente(app, escenario, "agenda")
    owner = Cliente(app, escenario, "owner_a", owner=True)
    assert (await sin("GET", "/local/settings")).status_code == 200
    assert (await sin("PATCH", "/local/settings", {"settings": {"x": 1}})).status_code == 403
    assert (await personal("PATCH", "/local/settings", {"settings": {"x": 1}})).status_code == 403  # permiso viejo
    assert (await agenda("PATCH", "/local/settings", {"settings": {"x": 1}})).status_code == 200
    assert (await owner("PATCH", "/local/settings", {"settings": {"x": 2}})).status_code == 200


async def test_aislamiento_de_settings(a, b, db_superuser, escenario):
    await a("PATCH", "/local/settings", {"settings": {"direccion": "Solo de A"}})
    assert (await b("GET", "/local/settings")).json() == {"settings": {}}
    await b("PATCH", "/local/settings", {"settings": {"direccion": "Solo de B"}})
    assert (await a("GET", "/local/settings")).json()["settings"]["direccion"] == "Solo de A"
    assert (await b("GET", "/local/settings")).json()["settings"]["direccion"] == "Solo de B"


async def test_editar_settings_no_toca_el_resto_del_tenant(a, escenario, db_superuser):
    antes = await db_superuser.fetchrow(
        "select status, plan, name, slug from public.tenants where id = $1", escenario["tenant"]["a"]
    )
    await a("PATCH", "/local/settings", {"settings": {"status": "suspended", "plan": "x", "name": "Hackeado"}})
    despues = await db_superuser.fetchrow(
        "select status, plan, name, slug from public.tenants where id = $1", escenario["tenant"]["a"]
    )
    assert dict(antes) == dict(despues)


# --- auditoría de los cambios de catálogo y settings -----------------------------------------------------------------


async def _auditoria(db_superuser, escenario):
    filas = await db_superuser.fetch(
        "select action, entity, entity_id, actor_type, actor_id, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 order by created_at, id",
        escenario["tenant"]["a"],
    )
    return [{**dict(f), "metadata": json.loads(f["metadata"]) if f["metadata"] else None} for f in filas]


async def test_servicios_auditan_alta_edicion_con_precio_y_baja(app, escenario, db_superuser):
    precios = Cliente(app, escenario, "precios")
    sid = (await precios("POST", "/servicios", SERVICIO)).json()["id"]
    await precios("PATCH", f"/servicios/{sid}", {"price": 18000.5, "duration_minutes": 45})
    await precios("PATCH", f"/servicios/{sid}", {"is_active": False})
    assert (await precios("DELETE", f"/servicios/{sid}")).status_code == 204
    filas = await _auditoria(db_superuser, escenario)
    assert [f["action"] for f in filas] == ["service_created", "service_updated", "service_updated", "service_deleted"]
    assert all(f["entity"] == "services" and str(f["entity_id"]) == sid for f in filas)
    assert all(f["actor_type"] == "staff" for f in filas)
    assert all(f["actor_id"] == escenario["usuario"]["precios"] for f in filas)
    assert filas[0]["metadata"]["precio"] == {"anterior": None, "nuevo": 15000}
    assert filas[1]["metadata"]["campos"] == ["duration_minutes", "price"]
    assert filas[1]["metadata"]["precio"] == {"anterior": 15000, "nuevo": 18000.5}
    assert filas[2]["metadata"] == {"campos": ["is_active"]}  # sin cambio de precio no hay bloque de precio
    assert filas[3]["metadata"] == {"precio": {"anterior": 18000.5, "nuevo": None}}
    assert "Corte" not in json.dumps([f["metadata"] for f in filas])  # sin nombres ni datos personales


async def test_un_servicio_que_falla_o_no_existe_no_deja_auditoria(app, escenario, db_superuser):
    precios = Cliente(app, escenario, "precios")
    sid = (await precios("POST", "/servicios", SERVICIO)).json()["id"]
    assert (await precios("PATCH", f"/servicios/{sid}", {"duration_minutes": -5})).status_code == 422
    assert (await precios("PATCH", f"/servicios/{uuid.uuid4()}", {"price": 1})).status_code == 404
    assert (await precios("DELETE", f"/servicios/{uuid.uuid4()}")).status_code == 404
    assert [f["action"] for f in await _auditoria(db_superuser, escenario)] == ["service_created"]


async def test_profesionales_auditan_alta_edicion_desactivacion_y_baja(app, escenario, db_superuser):
    personal = Cliente(app, escenario, "personal")
    pid = (await personal("POST", "/profesionales", {"name": "Ana Pérez"})).json()["id"]
    await personal("PATCH", f"/profesionales/{pid}", {"name": "Ana P."})
    await personal("PATCH", f"/profesionales/{pid}", {"is_active": False})
    assert (await personal("DELETE", f"/profesionales/{pid}")).status_code == 204
    filas = await _auditoria(db_superuser, escenario)
    assert [f["action"] for f in filas] == [
        "professional_created",
        "professional_updated",
        "professional_deactivated",
        "professional_deleted",
    ]
    assert all(f["entity"] == "professionals" and str(f["entity_id"]) == pid for f in filas)
    assert all(f["actor_id"] == escenario["usuario"]["personal"] for f in filas)
    assert filas[1]["metadata"] == {"campos": ["name"]}
    assert filas[2]["metadata"] == {"campos": ["is_active"]}
    assert "Ana" not in json.dumps([f["metadata"] for f in filas])


async def test_settings_auditan_las_claves_cambiadas_sin_sus_valores(app, escenario, db_superuser):
    agenda = Cliente(app, escenario, "agenda")
    r = await agenda(
        "PATCH",
        "/local/settings",
        {"settings": {"direccion": "Calle Falsa 123", "booking_approval_timeout_hours": 48}},
    )
    assert r.status_code == 200
    r = await agenda("PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": 0}})
    assert r.status_code == 422
    filas = await _auditoria(db_superuser, escenario)
    assert [f["action"] for f in filas] == ["settings_updated"]  # el rechazado no deja fila
    assert filas[0]["entity"] == "tenants" and filas[0]["entity_id"] == escenario["tenant"]["a"]
    assert filas[0]["actor_id"] == escenario["usuario"]["agenda"]
    assert filas[0]["metadata"] == {"claves": ["booking_approval_timeout_hours", "direccion"]}
    assert "Calle Falsa" not in json.dumps(filas[0]["metadata"])


async def test_disponibilidad_audita_reglas_y_bloqueos_sin_textos_libres(app, escenario, db_superuser):
    agenda = Cliente(app, escenario, "agenda")
    pid = (await Cliente(app, escenario, "personal")("POST", "/profesionales", {"name": "Ana"})).json()["id"]
    rid = (await agenda("POST", "/reglas-disponibilidad", _regla(pid))).json()["id"]
    await agenda("PATCH", f"/reglas-disponibilidad/{rid}", {"end_time": "20:00"})
    assert (await agenda("DELETE", f"/reglas-disponibilidad/{rid}")).status_code == 204
    bid = (await agenda("POST", "/bloqueos-disponibilidad", _bloqueo(reason="Motivo privado"))).json()["id"]
    await agenda("PATCH", f"/bloqueos-disponibilidad/{bid}", {"reason": "Otro motivo privado"})
    assert (await agenda("DELETE", f"/bloqueos-disponibilidad/{bid}")).status_code == 204
    assert (await agenda("DELETE", f"/bloqueos-disponibilidad/{bid}")).status_code == 404
    filas = [f for f in await _auditoria(db_superuser, escenario) if f["action"].startswith("availability_")]
    assert [f["action"] for f in filas] == [
        "availability_rule_created",
        "availability_rule_updated",
        "availability_rule_deleted",
        "availability_block_created",
        "availability_block_updated",
        "availability_block_deleted",
    ]
    assert all(f["actor_id"] == escenario["usuario"]["agenda"] for f in filas)
    assert filas[1]["metadata"] == {"campos": ["end_time"], "professional_id": pid}
    assert filas[4]["metadata"] == {"campos": ["reason"], "professional_id": None}
    assert "privado" not in json.dumps([f["metadata"] for f in filas])


async def test_asignacion_de_servicios_audita_el_cambio_de_modo(app, escenario, db_superuser):
    personal = Cliente(app, escenario, "personal")
    owner = Cliente(app, escenario, "owner_a", owner=True)
    pid, sid = await _profesional(personal), await _servicio(owner)
    ruta = f"/profesionales/{pid}/servicios/{sid}"
    await personal("PUT", ruta, {"confirmation_mode": "auto"})
    await personal("PUT", ruta, {"confirmation_mode": "approval"})
    assert (await personal("DELETE", ruta)).status_code == 204
    filas = [f for f in await _auditoria(db_superuser, escenario) if f["action"].startswith("professional_service_")]
    assert [f["action"] for f in filas] == [
        "professional_service_assigned",
        "professional_service_updated",
        "professional_service_removed",
    ]
    assert [f["metadata"]["confirmation_mode"] for f in filas] == [
        {"anterior": None, "nuevo": "auto"},
        {"anterior": "auto", "nuevo": "approval"},
        {"anterior": "approval", "nuevo": None},
    ]
    assert all(f["metadata"]["service_id"] == sid for f in filas)


# --- vínculo profesional-cuenta: solo el owner ----------------------------------------------------------------------


async def test_manage_staff_sin_owner_no_puede_vincular_ni_cambiar_ni_desvincular(app, escenario, a, db_superuser):
    personal = Cliente(app, escenario, "personal")
    yo = str(escenario["tenant_user"]["personal"])
    tercero = str(escenario["tenant_user"]["sin"])
    # vincularse a si mismo o a un tercero al crear: 403 y no se crea nada
    for uid in (yo, tercero):
        r = await personal("POST", "/profesionales", {"name": "X", "tenant_user_id": uid})
        assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.professionals where tenant_id = $1", escenario["tenant"]["a"]
        )
        == 0
    )
    # sin vinculo puede crear y editar
    pid = (await personal("POST", "/profesionales", {"name": "Ana"})).json()["id"]
    assert (await personal("PATCH", f"/profesionales/{pid}", {"name": "Ana 2"})).status_code == 200
    # vincular por PATCH: 403
    assert (await personal("PATCH", f"/profesionales/{pid}", {"tenant_user_id": yo})).status_code == 403
    # el owner vincula; luego manage_staff no puede cambiar ni desvincular
    assert (await a("PATCH", f"/profesionales/{pid}", {"tenant_user_id": tercero})).status_code == 200
    assert (await personal("PATCH", f"/profesionales/{pid}", {"tenant_user_id": yo})).status_code == 403
    assert (await personal("PATCH", f"/profesionales/{pid}", {"tenant_user_id": None})).status_code == 403
    # repetir el mismo vinculo no es un cambio; editar otros campos tampoco lo toca
    assert (
        await personal("PATCH", f"/profesionales/{pid}", {"tenant_user_id": tercero, "name": "Z"})
    ).status_code == 200
    assert (await personal("PATCH", f"/profesionales/{pid}", {"is_active": False})).status_code == 200
    vinculo = await db_superuser.fetchval(
        "select tenant_user_id from public.professionals where id = $1", uuid.UUID(pid)
    )
    assert str(vinculo) == tercero


async def test_el_owner_vincula_y_desvincula_y_el_audit_lleva_ids_anterior_y_nuevo(a, escenario, db_superuser):
    uid = str(escenario["tenant_user"]["personal"])
    pid = await _profesional(a, tenant_user_id=uid)
    assert (await a("PATCH", f"/profesionales/{pid}", {"tenant_user_id": None})).status_code == 200
    otro = str(escenario["tenant_user"]["sin"])
    assert (await a("PATCH", f"/profesionales/{pid}", {"tenant_user_id": otro})).status_code == 200
    filas = await _auditoria(db_superuser, escenario)
    assert [f["metadata"]["vinculo"] for f in filas] == [
        {"anterior": None, "nuevo": uid},
        {"anterior": uid, "nuevo": None},
        {"anterior": None, "nuevo": otro},
    ]


async def test_un_ex_owner_con_jwt_de_owner_no_puede_cambiar_el_vinculo(app, escenario, db_superuser):
    """El owner se lee en vivo: un token que dice owner pero la base dice que no, recibe 403."""
    falso = Cliente(app, escenario, "personal", owner=True)
    r = await falso(
        "POST", "/profesionales", {"name": "X", "tenant_user_id": str(escenario["tenant_user"]["personal"])}
    )
    assert r.status_code == 403
