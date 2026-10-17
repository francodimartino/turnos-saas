"""Audit de /auth/* contra Postgres real: el flujo deja filas en `audit_log` (rol `dashboard_service`)."""

import uuid

import pytest

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http, sesion_json, token  # noqa: E402
from dashboard_backend.tests.test_integracion import _PoolDeUnaConexion, escenario  # noqa: E402, F401

ORIGEN_H = {"Origin": ORIGEN}


async def test_el_flujo_de_login_refresh_y_logout_queda_en_audit_log(conectar_como, escenario, db_superuser):  # noqa: F811
    conn = await conectar_como("dashboard_service")
    app, sb, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    owner = escenario["owner"]
    acceso = token(user_id=owner, tenant_id=escenario["a"], is_owner=True, aal="aal2")
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso, refresh="refresco-nuevo"))
    cliente = http(app)

    r = await cliente.post(
        "/auth/login", json={"email": "ana@ejemplo.test", "password": "clave-larga-123"}, headers=ORIGEN_H
    )
    assert r.status_code == 200
    r = await cliente.post("/auth/refrescar", headers={**ORIGEN_H, "Cookie": "dash_refresco=refresco-viejo"})
    assert r.status_code == 200
    sb.responder("POST", "/auth/v1/logout", 204)
    r = await cliente.post("/auth/logout", headers={**ORIGEN_H, "Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 204

    filas = await db_superuser.fetch(
        "select action, tenant_id, actor_id, actor_type, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action like 'auth!_%' escape '!' order by created_at, id",
        escenario["a"],
    )
    assert [f["action"] for f in filas] == ["auth_login_ok", "auth_refresh", "auth_logout"]
    assert all(f["actor_id"] == owner and f["actor_type"] == "tenant_user" for f in filas)
    assert all("ana@ejemplo.test" not in (f["metadata"] or "") for f in filas)


async def test_un_audit_que_la_base_rechaza_no_rompe_el_login(conectar_como, escenario):  # noqa: F811
    conn = await conectar_como("dashboard_service")
    app, sb, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    # tenant inexistente en el contexto: el insert del audit viola la FK, y el login igual responde 200.
    acceso = token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso))
    r = await http(app).post(
        "/auth/login", json={"email": "ana@ejemplo.test", "password": "clave-larga-123"}, headers=ORIGEN_H
    )
    assert r.status_code == 200
