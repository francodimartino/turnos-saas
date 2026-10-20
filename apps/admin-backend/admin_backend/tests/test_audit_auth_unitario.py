"""Audit de los eventos de autenticación del panel (login, MFA, refresh, logout) con piezas simuladas."""

import json
import secrets
import uuid

from admin_backend.tests.soporte import ORIGEN, app_de_prueba, bearer, http, sesion_json, token

FACTOR = "11111111-1111-1111-1111-111111111111"
VERIFICADO = {"id": FACTOR, "factor_type": "totp", "status": "verified"}
CABECERA = {"Origin": ORIGEN}
EMAIL = "secreto.admin@ejemplo.test"
PASSWORD = "pw-" + secrets.token_urlsafe(12)


def _acciones(pool) -> list[str]:
    return [a[0] for a in pool.conn.auditados]


def _todo_el_audit(pool) -> str:
    return json.dumps([[str(x) for x in a] for a in pool.conn.auditados])


async def test_login_exitoso_deja_audit_con_el_actor_y_sin_tenant():
    app, sb, pool = app_de_prueba()
    admin = uuid.uuid4()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(user_id=admin, aal="aal1")))
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=CABECERA)
    assert r.status_code == 200
    assert _acciones(pool) == ["auth_login_succeeded"]
    accion, entidad, entidad_id, metadata, tenant = pool.conn.auditados[0]
    assert entidad == "auth" and entidad_id == admin and tenant is None
    assert json.loads(metadata) == {"siguiente_paso": "enrolar_mfa"}
    # el actor se fija en la transacción (platform_admin lo deriva la base del rol)
    assert any("app.current_actor_id" in sql and args == (str(admin),) for sql, args in pool.conn.ejecutado)


async def test_login_fallido_deja_audit_sin_email_password_ni_ip():
    app, sb, pool = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error_code": "invalid_credentials"})
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=CABECERA)
    assert r.status_code == 401
    assert _acciones(pool) == ["auth_login_failed"]
    assert json.loads(pool.conn.auditados[0][3]) == {"codigo": "credenciales_invalidas"}
    crudo = _todo_el_audit(pool)
    assert EMAIL not in crudo and PASSWORD not in crudo and "127.0.0.1" not in crudo


async def test_login_de_cuenta_que_no_es_admin_deja_audit_fallido():
    app, sb, pool = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(is_platform_admin=False, aal="aal1")))
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=CABECERA)
    assert r.status_code == 403
    assert _acciones(pool) == ["auth_login_failed"]
    assert EMAIL not in _todo_el_audit(pool)


async def test_enrolar_y_verificar_mfa_dejan_audit():
    app, sb, pool = app_de_prueba()
    admin = uuid.uuid4()
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": []})
    sb.responder("POST", "/auth/v1/factors", 200, {"id": FACTOR, "totp": {"qr_code": "q", "secret": "s", "uri": "u"}})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder(
        "POST",
        f"/auth/v1/factors/{FACTOR}/verify",
        200,
        sesion_json(access=token(user_id=admin), factores=[VERIFICADO]),
    )
    async with http(app) as c:
        e = await c.post("/auth/mfa/enrolar", headers=bearer(user_id=admin, aal="aal1"))
        v = await c.post(
            "/auth/mfa/verificar",
            json={"factor_id": FACTOR, "codigo": "123456"},
            headers=bearer(user_id=admin, aal="aal1"),
        )
    assert e.status_code == 200 and v.status_code == 200
    assert _acciones(pool) == ["auth_mfa_enrolled", "auth_mfa_verified"]
    assert all(a[2] == admin and a[4] is None for a in pool.conn.auditados)


async def test_refresco_y_logout_dejan_audit():
    app, sb, pool = app_de_prueba()
    admin = uuid.uuid4()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(user_id=admin)))
    sb.responder("POST", "/auth/v1/logout", 204)
    async with http(app) as c:
        r = await c.post("/auth/refrescar", headers={**CABECERA, "Cookie": "adm_refresco=abc"})
        o = await c.post("/auth/logout", headers={**CABECERA, "Cookie": f"adm_acceso={token(user_id=admin)}"})
    assert r.status_code == 200 and o.status_code == 204
    assert r.json() == {"expira_en": 3600}  # solo cuándo vence: los tokens van en cookies
    assert _acciones(pool) == ["auth_session_refreshed", "auth_logged_out"]
    assert all(a[2] == admin for a in pool.conn.auditados)


async def test_si_falla_el_audit_el_login_sigue_funcionando(caplog):
    app, sb, pool = app_de_prueba()
    pool.conn.audit_falla = True
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(aal="aal1")))
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=CABECERA)
    assert r.status_code == 200 and r.json()["siguiente_paso"] == "enrolar_mfa"
    assert any(x.getMessage() == "audit_auth_fallido" for x in caplog.records)
    assert EMAIL not in caplog.text and PASSWORD not in caplog.text


async def test_codigo_totp_invalido_deja_auth_mfa_failed_con_el_actor_y_sin_codigo_ni_factor():
    app, sb, pool = app_de_prueba()
    admin = uuid.uuid4()
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 400, {"error_code": "mfa_verification_failed"})
    async with http(app) as c:
        r = await c.post(
            "/auth/mfa/verificar",
            json={"factor_id": FACTOR, "codigo": "654321"},
            headers=bearer(user_id=admin, aal="aal1"),
        )
    # la respuesta no cambia
    assert r.status_code == 401
    assert r.json()["detail"]["codigo"] == "codigo_mfa_invalido"
    assert _acciones(pool) == ["auth_mfa_failed"]
    accion, entidad, entidad_id, metadata, tenant = pool.conn.auditados[0]
    assert entidad == "auth" and entidad_id == admin and tenant is None
    assert json.loads(metadata) == {"codigo": "codigo_mfa_invalido"}
    crudo = _todo_el_audit(pool)
    assert "654321" not in crudo and FACTOR not in crudo


async def test_si_falla_el_audit_del_totp_invalido_sigue_el_mismo_401(caplog):
    app, sb, pool = app_de_prueba()
    pool.conn.audit_falla = True
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 400, {"error_code": "mfa_verification_failed"})
    async with http(app) as c:
        r = await c.post(
            "/auth/mfa/verificar", json={"factor_id": FACTOR, "codigo": "654321"}, headers=bearer(aal="aal1")
        )
    assert r.status_code == 401 and r.json()["detail"]["codigo"] == "codigo_mfa_invalido"
    assert any(x.getMessage() == "audit_auth_fallido" for x in caplog.records)
