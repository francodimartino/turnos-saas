"""Rate limit por IP/email y Turnstile en /auth/*: se rechaza antes de llegar a Supabase Auth."""

import dataclasses
import logging
import uuid

import httpx
import pytest
from turnos_proteccion_login import ConfigIp, LimitesLogin, VerificadorTurnstile

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, config_prueba, http, sesion_json, token

ORIGEN_H = {"Origin": ORIGEN}
TENANT = uuid.uuid4()
EMAIL = "ana@ejemplo.test"
CLAVE = "clave-larga-123"


def _config(por_ip=100, por_email=100, **extra):
    return config_prueba(login_limites=LimitesLogin(por_ip=por_ip, por_email=por_email, ventana_seg=900), **extra)


def _login(cliente, email=EMAIL, password=None, headers=None, **extra):
    return cliente.post(
        "/auth/login",
        json={"email": email, "password": password or CLAVE, **extra},
        headers={**ORIGEN_H, **(headers or {})},
    )


def _ok(sb):
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(tenant_id=TENANT)))


def _mal(sb):
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})


def _turnstile(exito=True, estado=200):
    llamadas = []

    def atender(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(estado, json={"success": exito})

    return VerificadorTurnstile("secreto-de-prueba", transport=httpx.MockTransport(atender)), llamadas


# ------------------------------------------------------------ límite por IP


async def test_login_supera_el_limite_por_ip_con_429_y_retry_after():
    app, sb, _ = app_de_prueba(config=_config(por_ip=2))
    _ok(sb)
    c = http(app)
    assert (await _login(c)).status_code == 200
    assert (await _login(c)).status_code == 200
    llamadas = len(sb.llamadas)
    r = await _login(c)
    assert r.status_code == 429
    assert r.json()["detail"]["codigo"] == "demasiados_intentos"
    assert int(r.headers["retry-after"]) >= 1
    assert len(sb.llamadas) == llamadas  # no llegó a Supabase


async def test_el_limite_por_ip_aplica_a_mfa_e_invitacion():
    cuerpos = {
        "/auth/mfa/verificar": {"factor_id": str(uuid.uuid4()), "codigo": "123456"},
        "/auth/invitacion/aceptar": {"token_hash": "abc", "password": "clave-larga-123"},
    }
    for ruta, cuerpo in cuerpos.items():
        app, _, _ = app_de_prueba(config=_config(por_ip=1))
        c = http(app)
        await c.post(ruta, json=cuerpo, headers=ORIGEN_H)  # 1.er intento: cuenta, sea cual sea su resultado
        r = await c.post(ruta, json=cuerpo, headers=ORIGEN_H)
        assert r.status_code == 429, ruta
        assert r.json()["detail"]["codigo"] == "demasiados_intentos"
        assert "retry-after" in r.headers


async def test_ip_distinta_no_comparte_contador():
    app, sb, _ = app_de_prueba(config=_config(por_ip=1, config_ip=ConfigIp(header="CF-Connecting-IP")))
    _ok(sb)
    c = http(app)
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.1"})).status_code == 200
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.1"})).status_code == 429
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.2"})).status_code == 200


async def test_header_de_ip_falsificado_se_ignora_sin_header_configurado(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "CF-Connecting-IP")  # el entorno posterior a la Config no cuenta
    app, sb, _ = app_de_prueba(config=_config(por_ip=1))
    _ok(sb)
    c = http(app)
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.1"})).status_code == 200
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.2"})).status_code == 429


# ----------------------------------------------------------- límite por email


async def test_login_bloquea_el_email_tras_n_fallos_aunque_la_clave_sea_correcta_despues():
    app, sb, _ = app_de_prueba(config=_config(por_email=2))
    _mal(sb)
    c = http(app)
    assert (await _login(c)).status_code == 401
    assert (await _login(c)).status_code == 401
    _ok(sb)
    llamadas = len(sb.llamadas)
    r = await _login(c)
    assert r.status_code == 429
    assert r.json()["detail"]["codigo"] == "demasiados_intentos"
    assert int(r.headers["retry-after"]) >= 1
    assert len(sb.llamadas) == llamadas
    # otro email no se afecta
    assert (await _login(c, email="otra@ejemplo.test")).status_code == 200


async def test_los_logins_correctos_no_cuentan_contra_el_email():
    app, sb, _ = app_de_prueba(config=_config(por_email=2))
    _ok(sb)
    c = http(app)
    for _ in range(6):
        assert (await _login(c)).status_code == 200


async def test_fallos_bajo_el_limite_no_bloquean_un_login_correcto_desde_otra_ip():
    app, sb, _ = app_de_prueba(config=_config(por_email=3, config_ip=ConfigIp(header="CF-Connecting-IP")))
    _mal(sb)
    c = http(app)
    for _ in range(2):
        assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.9"})).status_code == 401
    _ok(sb)
    assert (await _login(c, headers={"CF-Connecting-IP": "203.0.113.10"})).status_code == 200


# ---------------------------------------------------------------- Turnstile


@pytest.mark.parametrize("extra", [{}, {"turnstile_token": ""}])
async def test_con_turnstile_activo_sin_token_es_400_y_no_llega_a_supabase(extra):
    verificador, llamadas = _turnstile()
    app, sb, _ = app_de_prueba(config=_config(), verificador_turnstile=verificador)
    _ok(sb)
    r = await _login(http(app), **extra)
    assert r.status_code == 400
    assert r.json()["detail"]["codigo"] == "turnstile_invalido"
    assert sb.llamadas == [] and llamadas == []


async def test_con_turnstile_activo_token_invalido_en_login_e_invitacion():
    verificador, _ = _turnstile(exito=False)
    app, sb, _ = app_de_prueba(config=_config(), verificador_turnstile=verificador)
    c = http(app)
    r = await _login(c, turnstile_token="malo")
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "turnstile_invalido"
    r = await c.post(
        "/auth/invitacion/aceptar",
        json={"token_hash": "abc", "password": "clave-larga-123", "turnstile_token": "malo"},
        headers=ORIGEN_H,
    )
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "turnstile_invalido"
    assert sb.llamadas == []


async def test_con_turnstile_activo_token_valido_pasa_y_no_se_envia_a_supabase():
    verificador, llamadas = _turnstile()
    app, sb, _ = app_de_prueba(config=_config(), verificador_turnstile=verificador)
    _ok(sb)
    r = await _login(http(app), turnstile_token="bueno")
    assert r.status_code == 200
    assert len(llamadas) == 1
    assert set(sb.cuerpo()) == {"email", "password"}


async def test_turnstile_caido_falla_cerrado_con_503():
    verificador, _ = _turnstile(estado=500)
    app, sb, _ = app_de_prueba(config=_config(), verificador_turnstile=verificador)
    _ok(sb)
    r = await _login(http(app), turnstile_token="x")
    assert r.status_code == 503
    assert r.json()["detail"]["codigo"] == "turnstile_no_disponible"
    assert sb.llamadas == []


async def test_turnstile_rechazado_no_cuenta_contra_el_email():
    verificador, _ = _turnstile(exito=False)
    app, sb, _ = app_de_prueba(config=_config(por_email=1), verificador_turnstile=verificador)
    _ok(sb)
    c = http(app)
    for _ in range(3):
        assert (await _login(c, turnstile_token="malo")).status_code == 400


async def test_sin_secreto_el_campo_es_opcional_y_login_funciona_como_antes():
    app, sb, _ = app_de_prueba(config=_config())
    _ok(sb)
    c = http(app)
    assert (await _login(c)).status_code == 200
    assert (await _login(c, turnstile_token="lo-que-sea")).status_code == 200


async def test_la_config_con_secreto_activa_turnstile_por_defecto():
    cfg = dataclasses.replace(_config(), turnstile_secret="s")
    app, sb, _ = app_de_prueba(config=cfg)
    _ok(sb)
    r = await _login(http(app))
    assert r.status_code == 400 and sb.llamadas == []


# -------------------------------------------------------------------- logs


async def test_los_rechazos_no_loguean_email_ip_ni_token(caplog):
    verificador, _ = _turnstile()
    app, sb, _ = app_de_prueba(
        config=_config(por_ip=1, por_email=1, config_ip=ConfigIp(header="CF-Connecting-IP")),
        verificador_turnstile=verificador,
    )
    _mal(sb)
    c = http(app)
    h = {"CF-Connecting-IP": "203.0.113.77"}
    with caplog.at_level(logging.DEBUG):
        await _login(c, headers=h, turnstile_token="token-secreto-xyz")
        await _login(c, headers=h, turnstile_token="token-secreto-xyz")
        await _login(c, headers={"CF-Connecting-IP": "203.0.113.78"}, turnstile_token="token-secreto-xyz")
    texto = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    for secreto in (EMAIL, "203.0.113.77", "203.0.113.78", "token-secreto-xyz", "clave-larga-123"):
        assert secreto not in texto
