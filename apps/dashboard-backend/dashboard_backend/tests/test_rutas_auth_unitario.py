"""Endpoints /auth: proxy de Supabase Auth con cookies HttpOnly (el navegador no ve tokens ni habla con Supabase)."""

import uuid

import pytest

from dashboard_backend.tests.soporte import (
    ORIGEN,
    SUPABASE_URL,
    app_de_prueba,
    http,
    sesion_json,
    token,
)

FACTOR = str(uuid.uuid4())
TENANT = uuid.uuid4()
ORIGEN_H = {"Origin": ORIGEN}


def _cookie(respuesta, nombre):
    """Devuelve el header Set-Cookie completo de `nombre` (o None)."""
    for valor in respuesta.headers.get_list("set-cookie"):
        if valor.startswith(f"{nombre}="):
            return valor
    return None


def _valor(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0].split("=", 1)[1]


def _sesion_con(sb, *, is_owner=False, aal="aal1", factores=(), tenant=TENANT):
    """Configura el login simulado con un access token verificable por el backend."""
    acceso = token(tenant_id=tenant, is_owner=is_owner, aal=aal)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso, factores=factores))
    return acceso


async def _login(cliente, **extra):
    cuerpo = {"email": "ana@ejemplo.test", "password": "clave-larga-123", **extra}
    return await cliente.post("/auth/login", json=cuerpo, headers=ORIGEN_H)


# ---------------------------------------------------------------- login


async def test_login_ok_pone_cookies_httponly_y_no_devuelve_tokens_en_el_body():
    app, sb, _ = app_de_prueba()
    acceso = _sesion_con(sb)
    r = await _login(http(app))
    assert r.status_code == 200
    assert r.json()["siguiente_paso"] == "listo"
    assert acceso not in r.text and "access_token" not in r.text and "refresh_token" not in r.text

    c_acceso = _cookie(r, "dash_acceso")
    c_refresco = _cookie(r, "dash_refresco")
    for c in (c_acceso, c_refresco):
        assert c is not None
        bajo = c.lower()
        assert "httponly" in bajo and "secure" in bajo and "samesite=strict" in bajo
    assert _valor(c_acceso) == acceso
    assert "path=/auth" in c_refresco.lower()  # el refresh solo viaja a /auth/*
    assert "path=/;" in c_acceso.lower() or c_acceso.lower().endswith("path=/")
    assert "no-store" in r.headers["cache-control"]


async def test_login_solo_manda_email_y_password_a_supabase_aunque_el_body_traiga_tenant_id():
    app, sb, _ = app_de_prueba()
    _sesion_con(sb)
    ajeno = str(uuid.uuid4())
    r = await _login(http(app), tenant_id=ajeno, is_owner=True, aal="aal2")
    assert r.status_code == 200
    assert set(sb.cuerpo()) == {"email", "password"}
    assert ajeno not in r.text


async def test_login_con_credenciales_malas_es_401_generico_y_sin_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    r = await _login(http(app))
    assert r.status_code == 401
    assert r.json()["detail"]["codigo"] == "credenciales_invalidas"
    assert "ana@ejemplo.test" not in r.text
    assert r.headers.get_list("set-cookie") == []


async def test_login_rate_limit_y_caida_de_supabase():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 429, {})
    assert (await _login(http(app))).status_code == 429
    sb.responder("POST", "/auth/v1/token", 503, {})
    assert (await _login(http(app))).status_code == 502


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"email": "sin-arroba", "password": "x"}, {"email": "a@b.co", "password": ""}, {"email": "a@b.co"}],
)
async def test_login_valida_la_forma_del_body(cuerpo):
    app, sb, _ = app_de_prueba()
    r = await http(app).post("/auth/login", json=cuerpo, headers=ORIGEN_H)
    assert r.status_code == 422
    assert sb.llamadas == []


async def test_login_siguiente_paso_segun_mfa():
    # owner sin factor: debe enrolar
    app, sb, _ = app_de_prueba()
    _sesion_con(sb, is_owner=True)
    assert (await _login(http(app))).json()["siguiente_paso"] == "enrolar_mfa"
    # con factor verificado y sesión aal1: debe verificar
    app, sb, _ = app_de_prueba()
    _sesion_con(sb, is_owner=True, factores=[{"id": FACTOR, "factor_type": "totp", "status": "verified"}])
    cuerpo = (await _login(http(app))).json()
    assert cuerpo["siguiente_paso"] == "verificar_mfa" and cuerpo["factor_id"] == FACTOR
    # staff sin factor ni owner: listo
    app, sb, _ = app_de_prueba()
    _sesion_con(sb, is_owner=False)
    assert (await _login(http(app))).json()["siguiente_paso"] == "listo"


async def test_login_de_alguien_sin_tenant_es_403_y_no_deja_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(tenant_id=None, is_platform_admin=True)))
    r = await _login(http(app))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "sin_acceso_dashboard"
    assert r.json()["detail"]["mensaje"] == (
        "Esta cuenta no tiene acceso a ningún negocio. "
        "Si administrás la plataforma, entrá por el panel de administración."
    )
    assert r.headers.get_list("set-cookie") == []


async def test_login_con_token_que_no_verifica_es_502_y_sin_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access="no-es-un-jwt"))
    r = await _login(http(app))
    assert r.status_code == 502
    assert r.headers.get_list("set-cookie") == []


# ---------------------------------------------------------------- CSRF / CORS


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://atacante.test"}, {"Origin": "null"}])
async def test_metodos_que_escriben_exigen_el_origen_del_dashboard(headers):
    app, sb, _ = app_de_prueba()
    _sesion_con(sb)
    r = await http(app).post("/auth/login", json={"email": "a@b.co", "password": "x"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "origen_no_permitido"
    assert sb.llamadas == []


async def test_un_post_con_cookie_de_sesion_desde_otro_origen_se_rechaza():
    app, sb, _ = app_de_prueba()
    r = await http(app).post("/auth/logout", headers={"Origin": "https://atacante.test", "Cookie": "dash_acceso=x"})
    assert r.status_code == 403
    assert sb.llamadas == []


async def test_preflight_cors_solo_para_el_origen_configurado():
    app, _, _ = app_de_prueba()
    pedido = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    ok = await http(app).options("/auth/login", headers={"Origin": ORIGEN, **pedido})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == ORIGEN
    assert ok.headers["access-control-allow-credentials"] == "true"
    mal = await http(app).options("/auth/login", headers={"Origin": "https://atacante.test", **pedido})
    assert "access-control-allow-origin" not in mal.headers


async def test_las_respuestas_llevan_cors_solo_para_el_origen_configurado():
    app, _, _ = app_de_prueba()
    r = await http(app).get("/health", headers={"Origin": "https://atacante.test"})
    assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------- cookie -> Authorization


async def test_la_cookie_de_acceso_autentica_los_endpoints_protegidos():
    app, _, _ = app_de_prueba()
    user = uuid.uuid4()
    acceso = token(user_id=user, tenant_id=TENANT)
    r = await http(app).get("/auth/sesion", headers={"Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 200
    assert r.json() == {
        "user_id": str(user),
        "tenant_id": str(TENANT),
        "is_owner": False,
        "aal": "aal1",
        "permisos": [],
    }


async def test_sesion_devuelve_los_permisos_vivos_del_staff():
    app, _, pool = app_de_prueba()
    pool.conn.permisos_staff = ["view_bookings", "view_reports"]
    acceso = token(tenant_id=TENANT)
    r = await http(app).get("/auth/sesion", headers={"Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 200
    assert r.json()["permisos"] == ["view_bookings", "view_reports"]


async def test_sesion_de_un_owner_vivo_devuelve_todo_el_catalogo_aunque_el_jwt_diga_otra_cosa():
    app, _, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    pool.conn.estado_staff = {"is_owner": True, "is_active": True, "tiene": False}
    acceso = token(tenant_id=TENANT, is_owner=True, aal="aal2")
    r = await http(app).get("/auth/sesion", headers={"Cookie": f"dash_acceso={acceso}"})
    assert r.json()["permisos"] == pool.conn.catalogo_permisos


async def test_sesion_no_cachea_permisos_en_el_jwt_los_lee_en_cada_request():
    app, _, pool = app_de_prueba()
    h = {"Cookie": f"dash_acceso={token(tenant_id=TENANT)}"}
    pool.conn.permisos_staff = ["view_bookings"]
    assert (await http(app).get("/auth/sesion", headers=h)).json()["permisos"] == ["view_bookings"]
    pool.conn.permisos_staff = []  # revocado: mismo token, siguiente request
    assert (await http(app).get("/auth/sesion", headers=h)).json()["permisos"] == []


async def test_sin_cookie_ni_header_es_401():
    app, _, _ = app_de_prueba()
    r = await http(app).get("/auth/sesion")
    assert r.status_code == 401


async def test_el_header_authorization_explicito_gana_sobre_la_cookie():
    app, _, _ = app_de_prueba()
    cookie = token(tenant_id=TENANT)
    r = await http(app).get(
        "/auth/sesion", headers={"Cookie": f"dash_acceso={cookie}", "Authorization": "Bearer basura"}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------- MFA


QR_DATA_URI = "data:image/svg+xml;charset=utf-8,%3Csvg/%3E"


async def test_enrolar_mfa_acepta_owner_aal1_y_usa_su_token_hacia_supabase():
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": []})
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "SECRETO", "uri": "otpauth://x"}},
    )
    acceso = token(tenant_id=TENANT, is_owner=True, aal="aal1")
    r = await http(app).post("/auth/mfa/enrolar", headers={**ORIGEN_H, "Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 200
    assert r.json() == {"factor_id": FACTOR, "qr_code": QR_DATA_URI, "secret": "SECRETO", "uri": "otpauth://x"}
    assert [(c.method, c.url.path) for c in sb.llamadas] == [("GET", "/auth/v1/user"), ("POST", "/auth/v1/factors")]
    assert all(c.headers["authorization"] == f"Bearer {acceso}" for c in sb.llamadas)
    assert "no-store" in r.headers["cache-control"]


FACTOR_VERIFICADO = {"id": FACTOR, "factor_type": "totp", "status": "verified"}
RESPUESTA_FACTOR = {"id": FACTOR, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "S", "uri": "otpauth://x"}}


def _owner(aal):
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    return app, sb, pool, token(tenant_id=TENANT, is_owner=True, aal=aal)


async def _enrolar(app, acceso):
    return await http(app).post("/auth/mfa/enrolar", headers={**ORIGEN_H, "Cookie": f"dash_acceso={acceso}"})


async def test_enrolar_mfa_en_aal1_con_factor_verificado_es_403_sin_audit_ni_alta(caplog):
    app, sb, pool, acceso = _owner("aal1")
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": [FACTOR_VERIFICADO]})
    sb.responder("POST", "/auth/v1/factors", 200, RESPUESTA_FACTOR)
    with caplog.at_level("WARNING"):
        r = await _enrolar(app, acceso)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "mfa_ya_configurada"
    assert ("POST", "/auth/v1/factors") not in [(c.method, c.url.path) for c in sb.llamadas]
    assert pool.conn.auditoria == []
    assert acceso not in caplog.text


async def test_enrolar_mfa_en_aal2_con_factor_verificado_si_pasa_sin_consultar():
    app, sb, _, acceso = _owner("aal2")
    sb.responder("POST", "/auth/v1/factors", 200, RESPUESTA_FACTOR)
    r = await _enrolar(app, acceso)
    assert r.status_code == 200
    assert [(c.method, c.url.path) for c in sb.llamadas] == [("POST", "/auth/v1/factors")]


async def test_enrolar_mfa_si_la_consulta_de_factores_falla_se_rechaza():
    app, sb, _, acceso = _owner("aal1")
    sb.responder("POST", "/auth/v1/factors", 200, RESPUESTA_FACTOR)
    sb.responder("GET", "/auth/v1/user", 500, {"msg": "x"})
    r = await _enrolar(app, acceso)
    assert r.status_code == 502
    sb.fallar_red("GET", "/auth/v1/user")
    assert (await _enrolar(app, acceso)).status_code == 502
    assert ("POST", "/auth/v1/factors") not in [(c.method, c.url.path) for c in sb.llamadas]


async def test_enrolar_mfa_sin_sesion_es_401_y_no_llama_a_supabase():
    app, sb, _ = app_de_prueba()
    r = await http(app).post("/auth/mfa/enrolar", headers=ORIGEN_H)
    assert r.status_code == 401
    assert sb.llamadas == []


async def test_enrolar_mfa_de_un_owner_ya_en_aal2_tambien_pasa_pero_otro_endpoint_con_aal1_no():
    app, _, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    acceso = token(tenant_id=TENANT, is_owner=True, aal="aal1")
    # El endpoint normal (sin excepción de enrollment) rechaza al owner aal1.
    r = await http(app).get("/auth/sesion", headers={"Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "mfa_requerida"


async def test_verificar_mfa_cambia_las_cookies_por_la_sesion_aal2():
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    aal2 = token(tenant_id=TENANT, is_owner=True, aal="aal2")
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 200, sesion_json(access=aal2))
    aal1 = token(tenant_id=TENANT, is_owner=True, aal="aal1")
    r = await http(app).post(
        "/auth/mfa/verificar",
        json={"factor_id": FACTOR, "codigo": "123456"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 200
    assert r.json()["siguiente_paso"] == "listo"
    assert _valor(_cookie(r, "dash_acceso")) == aal2
    assert aal2 not in r.text
    assert sb.llamadas[0].headers["authorization"] == f"Bearer {aal1}"


async def test_verificar_mfa_con_codigo_incorrecto_es_401_y_no_cambia_cookies():
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 400, {"error_code": "mfa_verification_failed"})
    aal1 = token(tenant_id=TENANT, is_owner=True, aal="aal1")
    r = await http(app).post(
        "/auth/mfa/verificar",
        json={"factor_id": FACTOR, "codigo": "000000"},
        headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["codigo"] == "codigo_mfa_invalido"
    assert r.headers.get_list("set-cookie") == []


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"factor_id": "no-uuid", "codigo": "123456"},
        {"factor_id": FACTOR, "codigo": "12345"},
        {"factor_id": FACTOR, "codigo": "abcdef"},
        {"factor_id": FACTOR},
    ],
)
async def test_verificar_mfa_valida_la_forma_del_body(cuerpo):
    app, sb, pool = app_de_prueba()
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    aal1 = token(tenant_id=TENANT, is_owner=True, aal="aal1")
    r = await http(app).post("/auth/mfa/verificar", json=cuerpo, headers={**ORIGEN_H, "Cookie": f"dash_acceso={aal1}"})
    assert r.status_code == 422
    assert sb.llamadas == []


# ---------------------------------------------------------------- invitación


async def test_aceptar_invitacion_define_la_contrasena_y_abre_sesion():
    app, sb, _ = app_de_prueba()
    acceso = token(tenant_id=TENANT)
    sb.responder("POST", "/auth/v1/verify", 200, sesion_json(access=acceso))
    sb.responder("PUT", "/auth/v1/user", 200, {"id": str(uuid.uuid4())})
    ajeno = str(uuid.uuid4())
    r = await http(app).post(
        "/auth/invitacion/aceptar",
        json={"token_hash": "hash-123", "password": "una-clave-nueva-123", "tenant_id": ajeno},
        headers=ORIGEN_H,
    )
    assert r.status_code == 200
    assert r.json()["siguiente_paso"] == "listo"
    assert _valor(_cookie(r, "dash_acceso")) == acceso
    assert acceso not in r.text
    assert [c.url.path for c in sb.llamadas] == ["/auth/v1/verify", "/auth/v1/user"]
    assert ajeno not in str(sb.llamadas[0].content) + str(sb.llamadas[1].content)


async def test_invitacion_vencida_es_400_y_sin_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/verify", 403, {"error_code": "otp_expired"})
    r = await http(app).post(
        "/auth/invitacion/aceptar", json={"token_hash": "x", "password": "una-clave-nueva-123"}, headers=ORIGEN_H
    )
    assert r.status_code == 400
    assert r.json()["detail"]["codigo"] == "invitacion_invalida"
    assert r.headers.get_list("set-cookie") == []


async def test_invitacion_con_contrasena_debil_es_422():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/verify", 200, sesion_json(access=token(tenant_id=TENANT)))
    sb.responder("PUT", "/auth/v1/user", 422, {"error_code": "weak_password"})
    r = await http(app).post(
        "/auth/invitacion/aceptar", json={"token_hash": "x", "password": "corta1"}, headers=ORIGEN_H
    )
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "contrasena_debil"


# ---------------------------------------------------------------- refresh y logout


async def test_refrescar_rota_las_cookies_con_el_refresh_token_de_la_cookie():
    app, sb, _ = app_de_prueba()
    nuevo = token(tenant_id=TENANT)
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=nuevo, refresh="refresco-nuevo"))
    r = await http(app).post("/auth/refrescar", headers={**ORIGEN_H, "Cookie": "dash_refresco=refresco-viejo"})
    assert r.status_code == 200
    assert sb.cuerpo() == {"refresh_token": "refresco-viejo"}
    assert _valor(_cookie(r, "dash_acceso")) == nuevo
    assert _valor(_cookie(r, "dash_refresco")) == "refresco-nuevo"


async def test_refrescar_sin_cookie_es_401():
    app, sb, _ = app_de_prueba()
    r = await http(app).post("/auth/refrescar", headers=ORIGEN_H)
    assert r.status_code == 401
    assert sb.llamadas == []


async def test_refresh_vencido_es_401_y_borra_las_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    r = await http(app).post("/auth/refrescar", headers={**ORIGEN_H, "Cookie": "dash_refresco=viejo"})
    assert r.status_code == 401
    assert "max-age=0" in _cookie(r, "dash_acceso").lower()
    assert "max-age=0" in _cookie(r, "dash_refresco").lower()


async def test_logout_cierra_en_supabase_y_borra_ambas_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/logout", 204)
    acceso = token(tenant_id=TENANT)
    r = await http(app).post("/auth/logout", headers={**ORIGEN_H, "Cookie": f"dash_acceso={acceso}"})
    assert r.status_code == 204
    assert sb.llamadas[0].headers["authorization"] == f"Bearer {acceso}"
    for nombre in ("dash_acceso", "dash_refresco"):
        c = _cookie(r, nombre).lower()
        assert "max-age=0" in c and "httponly" in c


async def test_logout_sin_sesion_igual_limpia_y_no_llama_a_supabase():
    app, sb, _ = app_de_prueba()
    r = await http(app).post("/auth/logout", headers=ORIGEN_H)
    assert r.status_code == 204
    assert sb.llamadas == []
    assert _cookie(r, "dash_acceso") is not None


async def test_el_navegador_nunca_recibe_la_url_de_supabase():
    app, sb, _ = app_de_prueba()
    _sesion_con(sb)
    r = await _login(http(app))
    assert SUPABASE_URL not in r.text
    assert SUPABASE_URL not in str(r.headers)
