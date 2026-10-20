"""/health, proxy de Auth (login/MFA con cookies HttpOnly) y gate de platform_admin con piezas simuladas."""

import pytest

from admin_backend.tests.soporte import (
    ORIGEN,
    PoolFalso,
    app_de_prueba,
    bearer,
    http,
    sesion_json,
    token,
)

FACTOR = "11111111-1111-1111-1111-111111111111"
VERIFICADO = {"id": FACTOR, "factor_type": "totp", "status": "verified"}


async def test_health_ok_y_sin_detalles_si_la_base_falla():
    pool = PoolFalso()
    app, _, _ = app_de_prueba(pool=pool)
    async with http(app) as c:
        assert (await c.get("/health")).json() == {"estado": "ok"}
        pool.conn.ping_falla = True
        r = await c.get("/health")
    assert r.status_code == 503
    assert "detalle interno" not in r.text


async def test_login_admin_aal1_con_factor_pide_mfa_y_pone_cookies_httponly():
    app, sb, _ = app_de_prueba()
    acceso = token(aal="aal1")
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=acceso, factores=[VERIFICADO]))
    async with http(app) as c:
        r = await c.post(
            "/auth/login", json={"email": "Admin@Ejemplo.test", "password": "x"}, headers={"Origin": ORIGEN}
        )
    assert r.status_code == 200
    assert r.json()["siguiente_paso"] == "verificar_mfa" and r.json()["factor_id"] == FACTOR
    assert acceso not in r.text  # los tokens nunca van en el body
    cookies = r.headers.get_list("set-cookie")
    assert any(ck.startswith("adm_acceso=") and "HttpOnly" in ck and "Secure" in ck for ck in cookies)
    assert any(ck.startswith("adm_refresco=") and "Path=/auth" in ck for ck in cookies)
    assert sb.cuerpo()["email"] == "admin@ejemplo.test"


async def test_login_admin_sin_factor_pide_enrolar():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(aal="aal1")))
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.co", "password": "x"}, headers={"Origin": ORIGEN})
    assert r.json()["siguiente_paso"] == "enrolar_mfa"


async def test_login_de_cuenta_que_no_es_platform_admin_da_403_sin_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(is_platform_admin=False, aal="aal1")))
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.co", "password": "x"}, headers={"Origin": ORIGEN})
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "sin_acceso_admin"
    assert "set-cookie" not in r.headers


async def test_login_credenciales_invalidas_401():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/token", 400, {"error_code": "invalid_credentials"})
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.co", "password": "x"}, headers={"Origin": ORIGEN})
    assert r.status_code == 401


async def test_enrolar_mfa_acepta_aal1_de_admin_pero_no_de_otros():
    app, sb, _ = app_de_prueba()
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": []})
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "totp": {"qr_code": '<svg fill="#fff"></svg>', "secret": "s", "uri": "otpauth://x"}},
    )
    async with http(app) as c:
        ok = await c.post("/auth/mfa/enrolar", headers=bearer(aal="aal1"))
        otro = await c.post("/auth/mfa/enrolar", headers=bearer(aal="aal1", is_platform_admin=False))
    assert ok.status_code == 200 and ok.json()["factor_id"] == FACTOR
    assert ok.json()["qr_code"].startswith("data:image/svg+xml;charset=utf-8,%3Csvg%20fill%3D%22%23fff%22")
    assert otro.status_code == 403


RESPUESTA_FACTOR = {"id": FACTOR, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "S", "uri": "otpauth://x"}}


def _rutas_enrolar(sb, usuario_get=None):
    sb.responder("POST", "/auth/v1/factors", 200, RESPUESTA_FACTOR)
    if usuario_get is not None:
        sb.responder("GET", "/auth/v1/user", *usuario_get)


def _metodos(sb):
    return [(c.method, c.url.path) for c in sb.llamadas]


async def test_enrolar_mfa_en_aal1_con_factor_verificado_es_403_sin_audit_ni_alta(caplog):
    app, sb, pool = app_de_prueba()
    _rutas_enrolar(sb, (200, {"id": FACTOR, "factors": [VERIFICADO]}))
    cabeceras = bearer(aal="aal1")
    with caplog.at_level("WARNING"):
        async with http(app) as c:
            r = await c.post("/auth/mfa/enrolar", headers=cabeceras)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "mfa_ya_configurada"
    assert ("POST", "/auth/v1/factors") not in _metodos(sb)
    assert pool.conn.auditados == []
    assert cabeceras["Authorization"].split()[-1] not in caplog.text


async def test_enrolar_mfa_en_aal2_con_factor_verificado_pasa_sin_consultar():
    app, sb, _ = app_de_prueba()
    _rutas_enrolar(sb)
    async with http(app) as c:
        r = await c.post("/auth/mfa/enrolar", headers=bearer(aal="aal2"))
    assert r.status_code == 200
    assert _metodos(sb) == [("POST", "/auth/v1/factors")]


async def test_enrolar_mfa_si_la_consulta_de_factores_falla_se_rechaza():
    app, sb, _ = app_de_prueba()
    _rutas_enrolar(sb, (500, {"msg": "x"}))
    async with http(app) as c:
        r = await c.post("/auth/mfa/enrolar", headers=bearer(aal="aal1"))
        sb.fallar_red("GET", "/auth/v1/user")
        r2 = await c.post("/auth/mfa/enrolar", headers=bearer(aal="aal1"))
    assert r.status_code == 502 and r2.status_code == 502
    assert ("POST", "/auth/v1/factors") not in _metodos(sb)


async def test_aal1_no_sirve_para_nada_fuera_del_enrollment():
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        for ruta in ("/tenants", "/settings", "/auth/sesion"):
            r = await c.get(ruta, headers=bearer(aal="aal1"))
            assert r.status_code == 403, ruta
            assert r.json()["detail"]["codigo"] == "mfa_requerida"


async def test_verificar_mfa_reemplaza_cookies_con_sesion_aal2():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 200, sesion_json(access=token(), factores=[VERIFICADO]))
    async with http(app) as c:
        r = await c.post(
            "/auth/mfa/verificar", json={"factor_id": FACTOR, "codigo": "123456"}, headers=bearer(aal="aal1")
        )
    assert r.status_code == 200 and r.json()["siguiente_paso"] == "listo"
    assert any(ck.startswith("adm_acceso=") for ck in r.headers.get_list("set-cookie"))


async def test_cookie_de_acceso_se_convierte_en_bearer_y_los_metodos_que_escriben_exigen_origen():
    app, _, _ = app_de_prueba()
    cookie = {"Cookie": f"adm_acceso={token()}"}
    ruta = "/tenants/00000000-0000-0000-0000-000000000001/suspender"
    async with http(app) as c:
        ok = await c.get("/auth/sesion", headers=cookie)
        sin_origen = await c.post(ruta, headers=cookie)
        origen_ajeno = await c.post(ruta, headers={**cookie, "Origin": "https://malo.test"})
    assert ok.status_code == 200
    # solo `user_id` y `aal`: las claves del dashboard (tenant, rol, permisos) no existen en el admin
    assert set(ok.json()) == {"user_id", "aal"} and ok.json()["aal"] == "aal2"
    assert isinstance(ok.json()["user_id"], str)
    assert sin_origen.status_code == 403 and sin_origen.json()["detail"]["codigo"] == "origen_no_permitido"
    assert origen_ajeno.status_code == 403


async def test_admin_quitado_de_platform_admins_pierde_acceso_aunque_el_claim_diga_que_si():
    pool = PoolFalso()
    pool.conn.es_admin = False
    app, _, _ = app_de_prueba(pool=pool)
    async with http(app) as c:
        r = await c.get("/auth/sesion", headers=bearer())
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_platform_admin"


async def test_sin_token_401():
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get("/tenants")).status_code == 401


async def test_refrescar_sin_cookie_401_y_logout_borra_cookies():
    app, sb, _ = app_de_prueba()
    sb.responder("POST", "/auth/v1/logout", 204)
    async with http(app) as c:
        assert (await c.post("/auth/refrescar", headers={"Origin": ORIGEN})).status_code == 401
        r = await c.post("/auth/logout", headers={"Origin": ORIGEN, "Cookie": f"adm_acceso={token()}"})
    assert r.status_code == 204
    assert any("adm_acceso=" in ck and "Max-Age=0" in ck for ck in r.headers.get_list("set-cookie"))


@pytest.mark.parametrize("ruta", ["/docs", "/openapi.json"])
async def test_sin_documentacion_publica(ruta):
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get(ruta)).status_code == 404


async def test_health_fallido_es_warning_conciso_con_la_clase_y_nunca_error(caplog):
    import logging

    pool = PoolFalso()
    app, _, _ = app_de_prueba(pool=pool)
    pool.conn.ping_falla = True
    with caplog.at_level(logging.DEBUG, logger="admin_backend"):
        async with http(app) as c:
            for _ in range(5):
                r = await c.get("/health")
                assert r.status_code == 503 and r.json() == {"estado": "no_disponible"}
    fallidos = [x for x in caplog.records if "health_fallido" in x.getMessage()]
    assert len(fallidos) == 5
    for x in fallidos:
        assert x.levelno == logging.WARNING
        assert x.exc_info is None
        assert "ConnectionError" in x.getMessage()
        assert "detalle interno" not in x.getMessage()
    assert not [x for x in caplog.records if x.levelno >= logging.ERROR]
