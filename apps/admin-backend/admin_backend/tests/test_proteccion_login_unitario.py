"""Rate limit por IP/email y Turnstile en /auth/* del admin-backend (piezas simuladas, sin red)."""

import httpx
import pytest
from platform_auth import VerificadorJWT
from turnos_proteccion_login import ConfigIp, VerificadorTurnstile

from admin_backend.app import crear_app
from admin_backend.config import Config, ConfigInvalida
from admin_backend.tests.soporte import (
    ANON_KEY,
    AUD,
    ORIGEN,
    SECRETO,
    SUPABASE_URL,
    AuthAdminFalso,
    PoolFalso,
    SupabaseSimulado,
    config_prueba,
    http,
    sesion_json,
    token,
)

H = {"Origin": ORIGEN}
CRED = {"email": "a@b.co", "password": "x"}


def _app(config: Config, turnstile: VerificadorTurnstile | None = None):
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(aal="aal1")))
    app = crear_app(
        config,
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=PoolFalso(),
        pool_readonly=PoolFalso(),
        cliente_auth=sb.cliente(),
        cliente_auth_admin=AuthAdminFalso(),
        verificador_turnstile=turnstile,
        configurar_observabilidad=False,
    )
    return app, sb


async def test_login_supera_limite_por_ip_responde_429_con_retry_after():
    app, sb = _app(config_prueba(login_limite_ip=2, login_limite_email=100))
    async with http(app) as c:
        for i in range(2):  # emails distintos: solo la IP se acumula
            assert (await c.post("/auth/login", json={**CRED, "email": f"u{i}@b.co"}, headers=H)).status_code == 200
        r = await c.post("/auth/login", json={**CRED, "email": "u9@b.co"}, headers=H)
    assert r.status_code == 429
    assert r.json()["detail"]["codigo"] == "demasiados_intentos"
    assert int(r.headers["retry-after"]) >= 1
    assert len(sb.llamadas) == 2  # el rechazado no llegó a Supabase


async def test_login_supera_limite_por_email_responde_429_y_otro_email_no_se_afecta():
    app, sb = _app(config_prueba(login_limite_ip=100, login_limite_email=2))
    sb.responder("POST", "/auth/v1/token", 400, {"error_code": "invalid_credentials"})  # solo los fallos cuentan
    async with http(app) as c:
        for _ in range(2):
            assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 401
        sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(aal="aal1")))
        r = await c.post("/auth/login", json={**CRED, "email": "A@B.co"}, headers=H)  # mismo email normalizado
        otro = await c.post("/auth/login", json={**CRED, "email": "otro@b.co"}, headers=H)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "a@b.co" not in r.text
    assert otro.status_code == 200


async def test_el_limite_por_ip_aplica_a_mfa_verificar():
    app, _ = _app(config_prueba(login_limite_ip=1, login_limite_email=100))
    body = {"factor_id": "11111111-1111-1111-1111-111111111111", "codigo": "123456"}
    async with http(app) as c:
        primero = await c.post("/auth/mfa/verificar", json=body, headers=H)  # cuenta como intento, sea cual sea
        assert primero.status_code != 429
        mfa = await c.post("/auth/mfa/verificar", json=body, headers=H)
        assert mfa.status_code == 429 and "retry-after" in mfa.headers


async def test_la_ip_se_toma_del_header_configurado():
    cfg = config_prueba(login_limite_ip=1, login_limite_email=100, config_ip=ConfigIp(header="CF-Connecting-IP"))
    app, _ = _app(cfg)
    async with http(app) as c:
        a = await c.post("/auth/login", json=CRED, headers={**H, "CF-Connecting-IP": "203.0.113.1"})
        b = await c.post("/auth/login", json=CRED, headers={**H, "CF-Connecting-IP": "203.0.113.2"})
        c2 = await c.post("/auth/login", json=CRED, headers={**H, "CF-Connecting-IP": "203.0.113.1"})
    assert (a.status_code, b.status_code, c2.status_code) == (200, 200, 429)


def _transporte(exito: bool):
    return httpx.MockTransport(lambda _req: httpx.Response(200, json={"success": exito}))


async def test_con_turnstile_sin_token_o_invalido_da_400_y_no_llega_a_supabase():
    app, sb = _app(
        config_prueba(turnstile_secret_key="secreto-ts"),
        VerificadorTurnstile("secreto-ts", transport=_transporte(False)),
    )
    async with http(app) as c:
        sin = await c.post("/auth/login", json=CRED, headers=H)
        malo = await c.post("/auth/login", json={**CRED, "turnstile_token": "malo"}, headers=H)
    for r in (sin, malo):
        assert r.status_code == 400 and r.json()["detail"]["codigo"] == "turnstile_invalido"
    assert sb.llamadas == []


async def test_con_turnstile_token_valido_loguea():
    app, sb = _app(
        config_prueba(turnstile_secret_key="secreto-ts"),
        VerificadorTurnstile("secreto-ts", transport=_transporte(True)),
    )
    async with http(app) as c:
        r = await c.post("/auth/login", json={**CRED, "turnstile_token": "bueno"}, headers=H)
    assert r.status_code == 200 and len(sb.llamadas) == 1


async def test_turnstile_caido_falla_cerrado_con_503_distinto():
    caido = VerificadorTurnstile("secreto-ts", transport=httpx.MockTransport(lambda _r: httpx.Response(502)))
    app, sb = _app(config_prueba(turnstile_secret_key="secreto-ts"), caido)
    async with http(app) as c:
        r = await c.post("/auth/login", json={**CRED, "turnstile_token": "t"}, headers=H)
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "turnstile_no_disponible"
    assert sb.llamadas == []


async def test_sin_secret_el_login_funciona_como_hoy():
    app, sb = _app(config_prueba())
    async with http(app) as c:
        r = await c.post("/auth/login", json=CRED, headers=H)
    assert r.status_code == 200 and len(sb.llamadas) == 1


async def test_turnstile_rechazado_no_consume_el_cupo_del_email():
    cfg = config_prueba(turnstile_secret_key="secreto-ts", login_limite_ip=100, login_limite_email=1)
    app, _ = _app(cfg, VerificadorTurnstile("secreto-ts", transport=_transporte(False)))
    async with http(app) as c:
        for _ in range(3):
            r = await c.post("/auth/login", json={**CRED, "turnstile_token": "x"}, headers=H)
            assert r.status_code == 400


async def test_los_logs_no_incluyen_email_ip_ni_token(caplog):
    app, _ = _app(config_prueba(login_limite_ip=100, login_limite_email=1))
    caplog.set_level("DEBUG")
    async with http(app) as c:
        await c.post("/auth/login", json={**CRED, "email": "secreto@dominio.co"}, headers=H)
        await c.post("/auth/login", json={**CRED, "email": "secreto@dominio.co"}, headers=H)
    assert "secreto@dominio.co" not in caplog.text
    assert "127.0.0.1" not in caplog.text


ENTORNO = {
    "ADMIN_DATABASE_URL": "postgresql://u:p@localhost/db",
    "ADMIN_READONLY_DATABASE_URL": "postgresql://ro:p@localhost/db",
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_ANON_KEY": ANON_KEY,
    "ADMIN_ORIGIN": ORIGEN,
}


def test_config_defectos_de_proteccion_de_login():
    c = Config.desde_entorno(ENTORNO)
    assert (c.login_limite_ip, c.login_limite_email, c.login_ventana_seg) == (20, 5, 900)
    assert c.turnstile_secret_key == ""


def test_config_lee_limites_y_secret_y_no_muestra_el_secret():
    c = Config.desde_entorno(
        {
            **ENTORNO,
            "LOGIN_LIMITE_IP": "7",
            "LOGIN_LIMITE_EMAIL": "3",
            "LOGIN_VENTANA_SEG": "60",
            "TURNSTILE_SECRET_KEY": "ts-secreto",
        }
    )
    assert (c.login_limite_ip, c.login_limite_email, c.login_ventana_seg) == (7, 3, 60)
    assert c.turnstile_secret_key == "ts-secreto" and "ts-secreto" not in repr(c)


@pytest.mark.parametrize(
    "var,valor",
    [
        ("LOGIN_LIMITE_IP", "0"),
        ("LOGIN_LIMITE_EMAIL", "abc"),
        ("LOGIN_VENTANA_SEG", "-5"),
        ("LOGIN_IP_HEADER", "X-Real-IP"),
        ("LOGIN_PROXIES_CONFIABLES", "0"),
    ],
)
def test_config_rechaza_valores_invalidos_de_proteccion_de_login(var, valor):
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**ENTORNO, var: valor})


async def test_cambiar_el_entorno_despues_de_crear_la_app_no_tiene_efecto(monkeypatch):
    app, _ = _app(config_prueba(login_limite_ip=1, login_limite_email=100))  # sin header configurado
    monkeypatch.setenv("LOGIN_IP_HEADER", "CF-Connecting-IP")
    async with http(app) as c:
        a = await c.post("/auth/login", json=CRED, headers={**H, "CF-Connecting-IP": "203.0.113.1"})
        b = await c.post("/auth/login", json=CRED, headers={**H, "CF-Connecting-IP": "203.0.113.2"})
    assert (a.status_code, b.status_code) == (200, 429)


def test_config_arma_la_config_de_ip():
    assert Config.desde_entorno(ENTORNO).config_ip == ConfigIp()
    c = Config.desde_entorno({**ENTORNO, "LOGIN_IP_HEADER": "X-Forwarded-For", "LOGIN_PROXIES_CONFIABLES": "2"})
    assert c.config_ip == ConfigIp(header="x-forwarded-for", proxies_confiables=2)


def test_config_acepta_los_headers_de_ip_soportados():
    Config.desde_entorno({**ENTORNO, "LOGIN_IP_HEADER": "cf-connecting-ip"})
    Config.desde_entorno({**ENTORNO, "LOGIN_IP_HEADER": "X-Forwarded-For", "LOGIN_PROXIES_CONFIABLES": "2"})
