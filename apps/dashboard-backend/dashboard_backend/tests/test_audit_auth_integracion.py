"""Audit de /auth/* contra Postgres real: el flujo deja filas en `audit_log` (rol `dashboard_service`)."""

import uuid

import pytest

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http, sesion_json, token  # noqa: E402
from dashboard_backend.tests.test_integracion import _PoolDeUnaConexion, escenario  # noqa: E402, F401

ORIGEN_H = {"Origin": ORIGEN}


async def test_el_flujo_completo_de_auth_queda_en_audit_log(conectar_como, escenario, db_superuser):  # noqa: F811
    conn = await conectar_como("dashboard_service")
    app, sb, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    owner = escenario["owner"]
    aal1 = token(user_id=owner, tenant_id=escenario["a"], is_owner=True, aal="aal1")
    aal2 = token(user_id=owner, tenant_id=escenario["a"], is_owner=True, aal="aal2")
    factor = str(uuid.uuid4())
    cliente = http(app)
    cuerpo_login = {"email": "ana@ejemplo.test", "password": "clave-larga-123"}

    # login fallido: 401 y sin fila en audit_log
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    r = await cliente.post("/auth/login", json=cuerpo_login, headers=ORIGEN_H)
    assert r.status_code == 401

    # login exitoso (owner sin factor: debe enrolar)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=aal1, refresh="refresco-nuevo"))
    r = await cliente.post("/auth/login", json=cuerpo_login, headers=ORIGEN_H)
    assert r.status_code == 200

    # enrolamiento y verificación de MFA
    sb.responder("GET", "/auth/v1/user", 200, {"id": factor, "factors": []})
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": factor, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "S", "uri": "otpauth://x"}},
    )
    r = await cliente.post("/auth/mfa/enrolar", headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"})
    assert r.status_code == 200
    sb.responder("POST", f"/auth/v1/factors/{factor}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{factor}/verify", 200, sesion_json(access=aal2))
    r = await cliente.post(
        "/auth/mfa/verificar",
        json={"factor_id": factor, "codigo": "123456"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 200

    # refresh y logout
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=aal2, refresh="refresco-nuevo"))
    r = await cliente.post("/auth/refrescar", headers={**ORIGEN_H, "Cookie": "dash_refresco=refresco-viejo"})
    assert r.status_code == 200
    sb.responder("POST", "/auth/v1/logout", 204)
    r = await cliente.post("/auth/logout", headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal2}"})
    assert r.status_code == 204

    filas = await db_superuser.fetch(
        "select action, actor_id, actor_type, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action like 'auth!_%' escape '!' order by created_at, id",
        escenario["a"],
    )
    assert [f["action"] for f in filas] == [
        "auth_login_succeeded",
        "auth_mfa_enrolled",
        "auth_mfa_verified",
        "auth_session_refreshed",
        "auth_logged_out",
    ]
    assert all(f["actor_id"] == owner and f["actor_type"] == "staff" for f in filas)
    volcado = " ".join(f["metadata"] or "" for f in filas)
    assert "ana@ejemplo.test" not in volcado and "123456" not in volcado
    # el fallido no dejó ninguna fila en ningún tenant (no hay local al que atribuirlo)
    assert await db_superuser.fetchval("select count(*) from public.audit_log where action = 'auth_login_failed'") == 0


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


async def test_totp_invalido_e_invitacion_aceptada_quedan_en_audit_log(conectar_como, escenario, db_superuser):  # noqa: F811
    conn = await conectar_como("dashboard_service")
    app, sb, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    owner = escenario["owner"]
    aal1 = token(user_id=owner, tenant_id=escenario["a"], is_owner=True, aal="aal1")
    factor = str(uuid.uuid4())
    cliente = http(app)

    sb.responder("POST", f"/auth/v1/factors/{factor}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{factor}/verify", 400, {"error_code": "mfa_verification_failed"})
    r = await cliente.post(
        "/auth/mfa/verificar",
        json={"factor_id": factor, "codigo": "654321"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 401

    sb.responder("POST", "/auth/v1/verify", 200, sesion_json(access=aal1))
    sb.responder("PUT", "/auth/v1/user", 200, {"id": str(owner)})
    r = await cliente.post(
        "/auth/invitacion/aceptar", json={"token_hash": "hash-secreto", "password": "clave-larga-123"}, headers=ORIGEN_H
    )
    assert r.status_code == 200

    filas = await db_superuser.fetch(
        "select action, actor_id, actor_type, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action in ('auth_mfa_failed', 'auth_invitation_accepted') order by created_at, id",
        escenario["a"],
    )
    assert [f["action"] for f in filas] == ["auth_mfa_failed", "auth_invitation_accepted"]
    assert all(f["actor_id"] == owner for f in filas)
    volcado = " ".join(f["metadata"] or "" for f in filas)
    assert "654321" not in volcado and factor not in volcado and "hash-secreto" not in volcado
