"""Conexión de Google Calendar contra Postgres real con el rol `dashboard_service`: guardado por la función
SECURITY DEFINER, permisos por profesional, auditoría, Vault y aislamiento entre tenants. Google va con el HTTP
mockeado (sin credenciales reales); datos ficticios.
"""

import json
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_calendar import GoogleSimulado, app_calendar  # noqa: E402


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def _borrar(conn, sql, *args):
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Tenant A: owner, gestor (manage_staff), el profesional pA1 (con su cuenta, sin permisos), staff sin permisos
    y un profesional pA2 sin cuenta. Tenant B: owner y pB1."""
    sufijo = uuid.uuid4().hex[:8]
    ta, tb = uuid.uuid4(), uuid.uuid4()
    for t, n in ((ta, "a"), (tb, "b")):
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n.upper(), f"it-cal-{n}-{sufijo}"
        )
    quienes = {
        "owner": (ta, None),
        "gestor": (ta, ("manage_staff",)),
        "profesional": (ta, ()),
        "sin_permisos": (ta, ("view_bookings",)),
        "b_owner": (tb, None),
    }
    auth, filas = {}, {}
    for nombre, (tenant, claves) in quienes.items():
        auth_id = uuid.uuid4()
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
        auth[nombre], filas[nombre] = auth_id, tu
    profesionales = {}
    for nombre, tenant, vinculado in (
        ("pa1", ta, filas["profesional"]),
        ("pa2", ta, None),
        ("pb1", tb, None),
    ):
        profesionales[nombre] = await db_superuser.fetchval(
            "insert into public.professionals (tenant_id, name, tenant_user_id) values ($1, $2, $3) returning id",
            tenant,
            f"Profesional {nombre}",
            vinculado,
        )
    tenants = [ta, tb]
    try:
        yield {"a": ta, "b": tb, "auth": auth, "prof": profesionales}
    finally:
        secretos = [
            f["secret_id"]
            for f in await db_superuser.fetch(
                "select secret_id from public.tenant_integrations where tenant_id = any($1::uuid[]) "
                "and secret_id is not null",
                tenants,
            )
        ]
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute(
                "delete from public.tenant_user_permissions where tenant_id = any($1::uuid[])", tenants
            )
        await _borrar(db_superuser, "delete from public.tenant_integrations where tenant_id = any($1::uuid[])", tenants)
        await db_superuser.execute("delete from vault.secrets where id = any($1::uuid[])", secretos)
        await _borrar(db_superuser, "delete from public.professionals where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from auth.users where id = any($1::uuid[])", list(auth.values()))


@pytest.fixture
async def dashboard(conectar_como):
    return await conectar_como("dashboard_service")


def _h(escenario, quien, escribe=True):
    owner = quien in ("owner", "b_owner")
    tenant = escenario["b"] if quien.startswith("b_") else escenario["a"]
    h = {
        "Cookie": "dash_acceso="
        + token(user_id=escenario["auth"][quien], tenant_id=tenant, is_owner=owner, aal="aal2" if owner else "aal1")
    }
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _app(dashboard, google=None):
    app, _, google = app_calendar(pool=_PoolDeUnaConexion(dashboard), google=google)
    return app, google


def _resultado(respuesta) -> str:
    assert respuesta.status_code == 303, respuesta.text
    return parse_qs(urlsplit(respuesta.headers["location"]).query)["google_calendar"][0]


async def _volver_de_google(app, google, escenario, quien, tenant, profesional, **extra):
    state = extra.pop("state", None) or google.state_de(tenant, profesional)
    return await http(app).get(
        "/calendar/callback", params={"code": "codigo-de-google", "state": state}, headers=_h(escenario, quien, False)
    )


async def _integraciones(db, profesional):
    return await db.fetch(
        "select id, tenant_id, secret_id, config::text as config from public.tenant_integrations "
        "where professional_id = $1 and integration_type = 'google_calendar'",
        profesional,
    )


async def _auditoria(db, tenant):
    filas = await db.fetch(
        "select action, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action like '%google_calendar' order by created_at, id",
        tenant,
    )
    return [(f["action"], json.loads(f["metadata"] or "{}")) for f in filas]


# --- el profesional conecta el suyo, de punta a punta ------------------------------------------------------------


async def test_el_profesional_conecta_su_calendario_y_solo_se_guarda_el_refresh_token(
    dashboard, escenario, db_superuser
):
    app, google = _app(dashboard)
    pa1 = escenario["prof"]["pa1"]

    r = await http(app).post(f"/profesionales/{pa1}/calendar/conexion", headers=_h(escenario, "profesional"))
    assert r.status_code == 200, r.text
    state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]

    r = await http(app).get(
        "/calendar/callback",
        params={"code": "codigo-de-google", "state": state},
        headers=_h(escenario, "profesional", escribe=False),
    )
    assert _resultado(r) == "ok"
    assert len(google.pedidos) == 1

    filas = await _integraciones(db_superuser, pa1)
    assert len(filas) == 1 and filas[0]["tenant_id"] == escenario["a"]
    secreto = await db_superuser.fetchval(
        "select decrypted_secret from vault.decrypted_secrets where id = $1", filas[0]["secret_id"]
    )
    assert json.loads(secreto) == {"refresh_token": google.refresh_token}
    assert "access_token" not in secreto

    assert await _auditoria(db_superuser, escenario["a"]) == [
        ("connect_google_calendar", {"professional_id": str(pa1)})
    ]

    r = await http(app).get(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "profesional", escribe=False))
    assert r.json()["conectado"] is True and r.json()["conectado_desde"] is not None
    assert "secret" not in r.text and google.refresh_token not in r.text

    # Reconectar (otro consentimiento) actualiza la misma integración: no duplica filas ni secrets.
    r = await _volver_de_google(app, google, escenario, "profesional", escenario["a"], pa1)
    assert _resultado(r) == "ok"
    otra = await _integraciones(db_superuser, pa1)
    assert [f["id"] for f in otra] == [filas[0]["id"]] and otra[0]["secret_id"] == filas[0]["secret_id"]


async def test_quien_no_es_el_profesional_ni_gestiona_staff_no_conecta_a_otro(dashboard, escenario, db_superuser):
    app, google = _app(dashboard)
    pa2 = escenario["prof"]["pa2"]
    for quien in ("profesional", "sin_permisos"):
        r = await http(app).post(f"/profesionales/{pa2}/calendar/conexion", headers=_h(escenario, quien))
        assert r.status_code == 403, (quien, r.text)
        r = await _volver_de_google(app, google, escenario, quien, escenario["a"], pa2)
        assert _resultado(r) == "sin_permiso"
        assert (
            await http(app).delete(f"/profesionales/{pa2}/calendar", headers=_h(escenario, quien))
        ).status_code == 403
    # y tampoco el profesional ajeno ve su estado
    r = await http(app).get(f"/profesionales/{pa2}/calendar", headers=_h(escenario, "sin_permisos", escribe=False))
    assert r.status_code == 403
    assert google.pedidos == [] and await _integraciones(db_superuser, pa2) == []


async def test_gestor_y_owner_conectan_y_desconectan_cualquiera_del_tenant(dashboard, escenario, db_superuser):
    app, google = _app(dashboard)
    pa1, pa2 = escenario["prof"]["pa1"], escenario["prof"]["pa2"]
    for quien, prof in (("gestor", pa2), ("owner", pa1)):
        r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h(escenario, quien))
        assert r.status_code == 200, r.text
        r = await _volver_de_google(app, google, escenario, quien, escenario["a"], prof)
        assert _resultado(r) == "ok"
        assert len(await _integraciones(db_superuser, prof)) == 1

    r = await http(app).delete(f"/profesionales/{pa2}/calendar", headers=_h(escenario, "gestor"))
    assert r.status_code == 200 and r.json()["habia_conexion"] is True
    assert await _integraciones(db_superuser, pa2) == []
    r = await http(app).delete(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "owner"))
    assert r.json()["habia_conexion"] is True


# --- desconectar -------------------------------------------------------------------------------------------------


async def test_desconectar_borra_la_integracion_y_el_secret_de_vault_y_audita_una_vez(
    dashboard, escenario, db_superuser
):
    app, google = _app(dashboard)
    pa1 = escenario["prof"]["pa1"]
    r = await _volver_de_google(app, google, escenario, "profesional", escenario["a"], pa1)
    assert _resultado(r) == "ok"
    secret_id = (await _integraciones(db_superuser, pa1))[0]["secret_id"]

    r = await http(app).delete(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "profesional"))
    assert r.status_code == 200 and r.json() == {
        "profesional_id": str(pa1),
        "conectado": False,
        "habia_conexion": True,
    }
    assert await _integraciones(db_superuser, pa1) == []
    assert await db_superuser.fetchval("select count(*) from vault.secrets where id = $1", secret_id) == 0

    r = await http(app).delete(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "profesional"))
    assert r.json()["habia_conexion"] is False
    assert [a for a, _ in await _auditoria(db_superuser, escenario["a"])] == [
        "connect_google_calendar",
        "disconnect_google_calendar",
    ]
    r = await http(app).get(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "profesional", escribe=False))
    assert r.json()["conectado"] is False


# --- aislamiento entre tenants ----------------------------------------------------------------------------------


async def test_otro_tenant_no_ve_conecta_ni_desconecta_y_su_state_no_sirve(dashboard, escenario, db_superuser):
    app, google = _app(dashboard)
    pa1, pb1 = escenario["prof"]["pa1"], escenario["prof"]["pb1"]
    r = await _volver_de_google(app, google, escenario, "owner", escenario["a"], pa1)
    assert _resultado(r) == "ok"
    antes = await _integraciones(db_superuser, pa1)
    pedidos = len(google.pedidos)

    # El owner de B no ve ni toca al profesional de A.
    assert (
        await http(app).get(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "b_owner", escribe=False))
    ).status_code == 404
    assert (
        await http(app).post(f"/profesionales/{pa1}/calendar/conexion", headers=_h(escenario, "b_owner"))
    ).status_code == 404
    assert (
        await http(app).delete(f"/profesionales/{pa1}/calendar", headers=_h(escenario, "b_owner"))
    ).status_code == 404
    assert [f["id"] for f in await _integraciones(db_superuser, pa1)] == [f["id"] for f in antes]

    # Un state emitido para A, usado con la sesión de B (GET y POST): rechazado sin llamar a Google ni guardar.
    state_de_a = google.state_de(escenario["a"], pa1)
    r = await http(app).get(
        "/calendar/callback",
        params={"code": "c", "state": state_de_a},
        headers=_h(escenario, "b_owner", escribe=False),
    )
    assert _resultado(r) == "state_invalido"
    r = await http(app).post(
        "/calendar/callback", json={"code": "c", "state": state_de_a}, headers=_h(escenario, "b_owner")
    )
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "state_invalido"
    # ... y al revés: el state de B con la sesión de A.
    r = await _volver_de_google(app, google, escenario, "owner", escenario["b"], pb1)
    assert _resultado(r) == "state_invalido"
    assert await _integraciones(db_superuser, pb1) == []
    assert len(google.pedidos) == pedidos

    # Un state con el tenant de la sesión pero el profesional de otro tenant: la base lo oculta (RLS).
    r = await _volver_de_google(app, google, escenario, "owner", escenario["a"], pb1)
    assert _resultado(r) == "profesional_invalido"
    assert await _integraciones(db_superuser, pb1) == []

    # Cada tenant conecta a los suyos.
    r = await _volver_de_google(app, google, escenario, "b_owner", escenario["b"], pb1)
    assert _resultado(r) == "ok"
    assert (await _integraciones(db_superuser, pb1))[0]["tenant_id"] == escenario["b"]
    assert (
        await http(app).get(f"/profesionales/{pb1}/calendar", headers=_h(escenario, "owner", escribe=False))
    ).status_code == 404


async def test_state_vencido_o_de_otra_firma_no_conecta(dashboard, escenario, db_superuser):
    app, google = _app(dashboard)
    pa1 = escenario["prof"]["pa1"]
    for state in (google.state_de(escenario["a"], pa1, antes_seg=3600), GoogleSimulado().state_de(escenario["a"], pa1)):
        r = await _volver_de_google(app, google, escenario, "owner", escenario["a"], pa1, state=state)
        assert _resultado(r) == "state_invalido"
    assert google.pedidos == [] and await _integraciones(db_superuser, pa1) == []


# --- profesional inactivo y escritura directa -------------------------------------------------------------------


async def test_profesional_inactivo_no_se_conecta(dashboard, escenario, db_superuser):
    app, google = _app(dashboard)
    pa1 = escenario["prof"]["pa1"]
    await db_superuser.execute("update public.professionals set is_active = false where id = $1", pa1)
    r = await http(app).post(f"/profesionales/{pa1}/calendar/conexion", headers=_h(escenario, "owner"))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "profesional_inactivo"
    r = await _volver_de_google(app, google, escenario, "owner", escenario["a"], pa1)
    assert _resultado(r) == "profesional_invalido"
    assert google.pedidos == [] and await _integraciones(db_superuser, pa1) == []


async def test_dashboard_service_no_puede_escribir_tenant_integrations_directo(dashboard, escenario):
    pa1 = escenario["prof"]["pa1"]
    for sql in (
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2)",
        "update public.tenant_integrations set config = '{}' where tenant_id = $1 and professional_id = $2",
        "delete from public.tenant_integrations where tenant_id = $1 and professional_id = $2",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with dashboard.transaction():
                await dashboard.execute(sql, escenario["a"], pa1)
