"""VerificadorTurnstile con httpx.MockTransport, y que ningún módulo loguee datos sensibles."""

import contextlib
import logging

import httpx
import pytest

from turnos_proteccion_login import LimitadorIntentos, TurnstileNoDisponible, VerificadorTurnstile, ip_real


def transporte(handler):
    return httpx.MockTransport(handler)


async def test_apagado_sin_clave_no_llama_a_la_red(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)

    def handler(request):
        raise AssertionError("no debe haber llamadas de red")

    v = VerificadorTurnstile(transport=transporte(handler))
    assert not v.activo
    assert await v.verificar(None) is True
    assert await v.verificar("cualquiera") is True


async def test_activo_token_valido_e_invalido():
    vistos = []

    def handler(request):
        cuerpo = request.read().decode()
        vistos.append((str(request.url), cuerpo))
        return httpx.Response(200, json={"success": "tok-ok" in cuerpo})

    v = VerificadorTurnstile(secret="s3cr3t", transport=transporte(handler))
    assert v.activo
    assert await v.verificar("tok-ok", ip="1.2.3.4") is True
    assert await v.verificar("tok-malo") is False
    assert vistos[0][0] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    assert "secret=s3cr3t" in vistos[0][1] and "remoteip=1.2.3.4" in vistos[0][1]


async def test_token_ausente_falla_sin_llamar_a_la_red():
    def handler(request):
        raise AssertionError("no debe llamar")

    v = VerificadorTurnstile(secret="s", transport=transporte(handler))
    assert await v.verificar(None) is False
    assert await v.verificar("") is False


async def test_5xx_falla_cerrado_con_error_distinto():
    v = VerificadorTurnstile(secret="s", transport=transporte(lambda r: httpx.Response(503)))
    with pytest.raises(TurnstileNoDisponible):
        await v.verificar("tok")


async def test_error_de_red_falla_cerrado_con_error_distinto():
    def handler(request):
        raise httpx.ConnectError("caido")

    v = VerificadorTurnstile(secret="s", transport=transporte(handler))
    with pytest.raises(TurnstileNoDisponible):
        await v.verificar("tok")


async def test_respuesta_no_json_falla_cerrado():
    v = VerificadorTurnstile(secret="s", transport=transporte(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(TurnstileNoDisponible):
        await v.verificar("tok")


async def test_clave_desde_entorno(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "desde-env")
    v = VerificadorTurnstile(transport=transporte(lambda r: httpx.Response(200, json={"success": True})))
    assert v.activo and await v.verificar("t") is True


async def test_no_loguea_token_ni_ip_ni_email(caplog):
    caplog.set_level(logging.DEBUG)
    token, ip, email = "TOKEN-SECRETO-XYZ", "203.0.113.77", "ana@ejemplo.com"

    for resp in (httpx.Response(200, json={"success": False}), httpx.Response(500)):
        v = VerificadorTurnstile(secret="s", transport=transporte(lambda r, resp=resp: resp))
        with contextlib.suppress(TurnstileNoDisponible):
            await v.verificar(token, ip=ip)

    def boom(request):
        raise httpx.ConnectError(f"fallo {token} {ip}")

    with contextlib.suppress(TurnstileNoDisponible):
        await VerificadorTurnstile(secret="s", transport=transporte(boom)).verificar(token, ip=ip)

    lim = LimitadorIntentos(limite=1, ventana_seg=60)
    for clave in (email, email, ip, ip):
        lim.intentar(clave)

    class R:
        headers = {"x-forwarded-for": "9.9.9.9"}
        client = type("C", (), {"host": ip})()

    ip_real(R())
    assert caplog.records, "el test debe ejercitar logs reales"
    for secreto in (token, ip, email, "ana@"):
        assert secreto not in caplog.text
