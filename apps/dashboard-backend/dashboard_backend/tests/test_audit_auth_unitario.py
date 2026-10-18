"""Audit de eventos de autenticación (/auth/*): una fila por evento, sin PII, y sin romper el flujo si falla."""

import json
import secrets
import uuid

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http, sesion_json, token

FACTOR = str(uuid.uuid4())
TENANT = uuid.uuid4()
USUARIO = uuid.uuid4()
EMAIL = "ana@ejemplo.test"
CLAVE_USUARIO = "clave-" + secrets.token_urlsafe(12)
ORIGEN_H = {"Origin": ORIGEN}
ACCIONES = {
    "auth_login_succeeded",
    "auth_mfa_enrolled",
    "auth_mfa_verified",
    "auth_session_refreshed",
    "auth_logged_out",
    "auth_mfa_failed",
    "auth_invitation_accepted",
}


def _acciones(pool) -> list[str]:
    return [a[0] for a in pool.conn.auditoria]


def _tenant_y_actor(pool) -> tuple[str | None, str | None]:
    """El contexto con el que se escribió el audit: lo setea tenant_tx antes de llamar a write_audit_log."""
    seteos = [args for sql, args in pool.conn.ejecutado if "set_config" in sql]
    assert seteos, "el audit con tenant debe abrir una transacción con contexto"
    tenant, actor = seteos[-1][0], seteos[-1][1]
    return str(tenant), str(actor)


async def _login(cliente):
    return await cliente.post("/auth/login", json={"email": EMAIL, "password": CLAVE_USUARIO}, headers=ORIGEN_H)


def _sin_pii(pool):
    volcado = json.dumps(pool.conn.auditoria, default=str)
    assert EMAIL not in volcado and CLAVE_USUARIO not in volcado and "127.0.0.1" not in volcado


async def test_login_ok_deja_auth_login_succeeded_con_tenant_y_actor():
    app, sb, pool = app_de_prueba()
    acceso = token(user_id=USUARIO, tenant_id=TENANT)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso))
    r = await _login(http(app))
    assert r.status_code == 200
    assert _acciones(pool) == ["auth_login_succeeded"]
    assert _tenant_y_actor(pool) == (str(TENANT), str(USUARIO))
    assert json.loads(pool.conn.auditoria[0][3]) == {"siguiente_paso": "listo"}
    _sin_pii(pool)


async def test_login_fallido_no_escribe_audit_log_y_deja_un_log_sin_pii(caplog):
    app, sb, pool = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    with caplog.at_level("WARNING"):
        r = await _login(http(app))
    assert r.status_code == 401
    assert pool.conn.auditoria == []
    registros = [x for x in caplog.records if x.getMessage() == "auth_login_failed"]
    assert len(registros) == 1 and registros[0].motivo == "credenciales_invalidas"
    assert EMAIL not in caplog.text and CLAVE_USUARIO not in caplog.text


async def test_mfa_enrolar_y_verificar_dejan_su_fila():
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": []})
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "S", "uri": "otpauth://x"}},
    )
    aal1 = token(user_id=USUARIO, tenant_id=TENANT, is_owner=True, aal="aal1")
    cliente = http(app)
    r = await cliente.post("/auth/mfa/enrolar", headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"})
    assert r.status_code == 200
    assert _acciones(pool) == ["auth_mfa_enrolled"]
    assert _tenant_y_actor(pool) == (str(TENANT), str(USUARIO))

    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    aal2 = token(user_id=USUARIO, tenant_id=TENANT, is_owner=True, aal="aal2")
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 200, sesion_json(access=aal2))
    r = await cliente.post(
        "/auth/mfa/verificar",
        json={"factor_id": FACTOR, "codigo": "123456"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 200
    assert _acciones(pool) == ["auth_mfa_enrolled", "auth_mfa_verified"]
    assert "123456" not in json.dumps(pool.conn.auditoria, default=str)


async def test_refrescar_y_logout_dejan_su_fila():
    app, sb, pool = app_de_prueba()
    nuevo = token(user_id=USUARIO, tenant_id=TENANT)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=nuevo, refresh="refresco-nuevo"))
    cliente = http(app)
    r = await cliente.post("/auth/refrescar", headers={**ORIGEN_H, "Cookie": "dash_refresco=refresco-viejo"})
    assert r.status_code == 200
    sb.responder("POST", "/auth/v1/logout", 204)
    r = await cliente.post("/auth/logout", headers={**ORIGEN_H, "Cookie": f"dash_acceso={nuevo}"})
    assert r.status_code == 204
    assert _acciones(pool) == ["auth_session_refreshed", "auth_logged_out"]
    assert "refresco" not in json.dumps(pool.conn.auditoria, default=str)


async def test_si_el_audit_falla_el_login_sigue_y_el_log_no_lleva_pii(caplog):
    app, sb, pool = app_de_prueba()
    acceso = token(user_id=USUARIO, tenant_id=TENANT)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso))

    async def _explota(sql, *args):
        if "write_audit_log" in sql:
            raise RuntimeError(f"fallo con {EMAIL} y {CLAVE_USUARIO}")
        return 1

    pool.conn.fetchval = _explota
    with caplog.at_level("ERROR"):
        r = await _login(http(app))
    assert r.status_code == 200
    assert r.json()["siguiente_paso"] == "listo"
    assert "dash_acceso" in "".join(r.headers.get_list("set-cookie"))
    registros = [x for x in caplog.records if x.getMessage() == "audit_auth_fallo"]
    assert len(registros) == 1
    assert EMAIL not in caplog.text and CLAVE_USUARIO not in caplog.text


async def test_si_el_audit_falla_el_login_fallido_sigue_siendo_401():
    app, sb, pool = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})

    async def _explota(sql, *args):
        raise ConnectionError("base caída")

    pool.conn.fetchval = _explota
    r = await _login(http(app))
    assert r.status_code == 401
    assert r.json()["detail"]["codigo"] == "credenciales_invalidas"


async def test_el_audit_no_altera_el_rate_limit_del_login():
    from dashboard_backend.tests.soporte import config_prueba

    app, sb, pool = app_de_prueba(config=config_prueba())
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    cliente = http(app)
    codigos = [(await _login(cliente)).status_code for _ in range(40)]
    assert 429 in codigos
    primer_429 = codigos.index(429)
    assert set(codigos[:primer_429]) == {401}
    # los logins fallidos y los rechazos por límite no escriben audit_log
    assert pool.conn.auditoria == []
    assert set(_acciones(pool)) <= ACCIONES


async def test_codigo_totp_invalido_deja_auth_mfa_failed_con_tenant_actor_y_sin_codigo_ni_factor():
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 400, {"error_code": "mfa_verification_failed"})
    aal1 = token(user_id=USUARIO, tenant_id=TENANT, is_owner=True, aal="aal1")
    r = await http(app).post(
        "/auth/mfa/verificar",
        json={"factor_id": FACTOR, "codigo": "654321"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 401 and r.json()["detail"]["codigo"] == "codigo_mfa_invalido"
    assert _acciones(pool) == ["auth_mfa_failed"]
    assert _tenant_y_actor(pool) == (str(TENANT), str(USUARIO))
    assert json.loads(pool.conn.auditoria[0][3]) == {"codigo": "codigo_mfa_invalido"}
    volcado = json.dumps(pool.conn.auditoria, default=str)
    assert "654321" not in volcado and FACTOR not in volcado


async def test_aceptar_invitacion_deja_auth_invitation_accepted_con_tenant_y_actor():
    app, sb, pool = app_de_prueba()
    acceso = token(user_id=USUARIO, tenant_id=TENANT, aal="aal1")
    sb.responder("POST", "/auth/v1/verify", 200, sesion_json(access=acceso))
    sb.responder("PUT", "/auth/v1/user", 200, {"id": str(USUARIO)})
    r = await http(app).post(
        "/auth/invitacion/aceptar", json={"token_hash": "hash-invitacion", "password": CLAVE_USUARIO}, headers=ORIGEN_H
    )
    assert r.status_code == 200
    assert _acciones(pool) == ["auth_invitation_accepted"]
    assert _tenant_y_actor(pool) == (str(TENANT), str(USUARIO))
    volcado = json.dumps(pool.conn.auditoria, default=str)
    assert "hash-invitacion" not in volcado and CLAVE_USUARIO not in volcado


async def test_si_el_audit_de_la_invitacion_falla_la_sesion_se_entrega(caplog):
    app, sb, pool = app_de_prueba()
    sb.responder("POST", "/auth/v1/verify", 200, sesion_json(access=token(user_id=USUARIO, tenant_id=TENANT)))
    sb.responder("PUT", "/auth/v1/user", 200, {"id": str(USUARIO)})

    async def _explota(sql, *args):
        raise ConnectionError("base caída")

    pool.conn.fetchval = _explota
    with caplog.at_level("ERROR"):
        r = await http(app).post(
            "/auth/invitacion/aceptar", json={"token_hash": "h", "password": CLAVE_USUARIO}, headers=ORIGEN_H
        )
    assert r.status_code == 200
    assert "dash_acceso" in "".join(r.headers.get_list("set-cookie"))
    assert any(x.getMessage() == "audit_auth_fallo" for x in caplog.records)
