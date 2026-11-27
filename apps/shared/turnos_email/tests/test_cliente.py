import json
import logging

import httpx
import pytest

from turnos_email import ClienteEmail, ConfigEmail, ErrorEmail

KEY = "re_clave_secreta_123"
DESTINO = "persona.privada@ejemplo.com"
ASUNTO = "Asunto reservado"
TEXTO = "Cuerpo reservado del mensaje"


def _cliente(manejador, dormidas=None, **cfg):
    async def dormir(s):
        if dormidas is not None:
            dormidas.append(s)

    config = ConfigEmail(api_key=KEY, remitente="Agendemos <avisos@agendemos.ar>", **cfg)
    return ClienteEmail(config, transporte=httpx.MockTransport(manejador), dormir=dormir)


async def test_envia_a_resend_con_key_remitente_y_html():
    pedidos = []

    def m(req):
        pedidos.append(req)
        return httpx.Response(200, json={"id": "x"})

    await _cliente(m).enviar(DESTINO, ASUNTO, TEXTO, html="<p>hola</p>")
    (req,) = pedidos
    assert req.method == "POST" and req.url.path == "/emails"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {
        "from": "Agendemos <avisos@agendemos.ar>",
        "to": [DESTINO],
        "subject": ASUNTO,
        "text": TEXTO,
        "html": "<p>hola</p>",
    }


async def test_sin_html_no_manda_el_campo():
    pedidos = []

    def m(req):
        pedidos.append(json.loads(req.content))
        return httpx.Response(200)

    await _cliente(m).enviar(DESTINO, ASUNTO, TEXTO)
    assert "html" not in pedidos[0]


async def test_timeout_explicito_de_conexion_y_total():
    c = _cliente(lambda r: httpx.Response(200), timeout_conexion=3.0, timeout_total=9.0)
    t = c._http.timeout
    assert t.connect == 3.0 and t.read == 9.0


async def test_4xx_permanente_no_reintenta_ni_refleja_el_cuerpo():
    llamadas = []

    def m(req):
        llamadas.append(1)
        return httpx.Response(422, json={"message": f"destino invalido {DESTINO}"})

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 1
    assert e.value.estado_http == 422 and not e.value.reintentable
    assert DESTINO not in str(e.value) and "invalido" not in str(e.value)


async def test_429_respeta_retry_after_con_tope_y_luego_exito():
    respuestas = [
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(429, headers={"retry-after": "999"}),
    ]
    dormidas = []

    def m(req):
        return respuestas.pop(0) if respuestas else httpx.Response(200)

    await _cliente(m, dormidas, tope_espera=30.0).enviar(DESTINO, ASUNTO, TEXTO)
    assert dormidas == [7.0, 30.0]


async def test_5xx_reintenta_hasta_el_maximo_y_falla_reintentable():
    llamadas = []

    def m(req):
        llamadas.append(1)
        return httpx.Response(503, text=DESTINO)

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m, max_reintentos=2).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 3
    assert e.value.reintentable and e.value.estado_http == 503


async def test_timeout_se_reintenta_y_no_filtra_datos():
    llamadas = []

    def m(req):
        llamadas.append(1)
        raise httpx.ConnectTimeout(f"timeout hacia {req.url} {DESTINO} {KEY}", request=req)

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m, max_reintentos=1).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 2
    assert e.value.estado_http is None and e.value.reintentable
    for dato in (DESTINO, ASUNTO, TEXTO, KEY):
        assert dato not in str(e.value)


async def test_el_logging_no_incluye_destino_asunto_cuerpo_ni_key(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ErrorEmail):
        await _cliente(lambda r: httpx.Response(500, text=f"{DESTINO} {KEY}")).enviar(DESTINO, ASUNTO, TEXTO)
    assert caplog.records
    texto = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for dato in (DESTINO, ASUNTO, TEXTO, KEY):
        assert dato not in texto


def test_el_repr_de_la_config_no_muestra_la_key():
    assert KEY not in repr(ConfigEmail(api_key=KEY, remitente="a@agendemos.ar"))
