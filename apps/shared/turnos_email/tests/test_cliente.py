import asyncio
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

    await _cliente(m, dormidas, tope_espera=10.0, timeout_total=60.0).enviar(DESTINO, ASUNTO, TEXTO)
    assert dormidas == [7.0, 10.0]


async def test_5xx_reintenta_hasta_el_maximo_y_falla_reintentable():
    llamadas = []

    def m(req):
        llamadas.append(1)
        return httpx.Response(503, text=DESTINO)

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m, max_reintentos=2).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 3
    assert e.value.reintentable and e.value.estado_http == 503


async def test_fallo_de_red_no_se_reintenta_y_no_filtra_datos():
    llamadas = []

    def m(req):
        llamadas.append(1)
        raise httpx.ConnectTimeout(f"timeout hacia {req.url} {DESTINO} {KEY}", request=req)

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m, max_reintentos=1).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 1
    assert e.value.estado_http is None and not e.value.reintentable
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


@pytest.mark.parametrize(
    ("estado", "esperado"),
    [
        (200, False),
        (302, False),
        (399, False),
        (400, False),
        (404, False),
        (429, True),
        (500, True),
        (503, True),
        (599, True),
        (600, False),
    ],
)
def test_reintentable_exacto(estado, esperado):
    assert ErrorEmail(estado_http=estado).reintentable is esperado


async def test_3xx_no_se_reintenta():
    llamadas = []

    def m(req):
        llamadas.append(1)
        return httpx.Response(302, headers={"location": "https://otro.example"})

    with pytest.raises(ErrorEmail) as e:
        await _cliente(m).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 1 and not e.value.reintentable


async def test_plazo_total_corta_un_transporte_lento():
    async def lento(req):
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(ErrorEmail) as e:
        await _cliente(lento, timeout_total=0.05).enviar(DESTINO, ASUNTO, TEXTO)
    assert e.value.estado_http is None and not e.value.reintentable


async def test_si_la_espera_no_entra_en_el_plazo_lanza_el_ultimo_error_real():
    llamadas = []
    dormidas = []

    def m(req):
        llamadas.append(1)
        return httpx.Response(503)

    # El primer backoff (2 s) no entra en un plazo de 0,5 s: no se espera ni se reintenta.
    with pytest.raises(ErrorEmail) as e:
        await _cliente(m, dormidas, timeout_total=0.5).enviar(DESTINO, ASUNTO, TEXTO)
    assert len(llamadas) == 1 and dormidas == []
    assert e.value.estado_http == 503 and e.value.reintentable


async def test_retry_after_mayor_que_el_plazo_restante_lanza_el_429():
    dormidas = []
    resp = httpx.Response(429, headers={"retry-after": "20"})
    with pytest.raises(ErrorEmail) as e:
        await _cliente(lambda r: resp, dormidas, timeout_total=10.0, tope_espera=30.0).enviar(DESTINO, ASUNTO, TEXTO)
    assert dormidas == []
    assert e.value.estado_http == 429 and e.value.reintentable and e.value.retry_after == 20.0


async def test_backoff_ante_503_sin_retry_after():
    dormidas = []
    with pytest.raises(ErrorEmail):
        await _cliente(
            lambda r: httpx.Response(503), dormidas, max_reintentos=4, timeout_total=100.0, tope_espera=5.0
        ).enviar(DESTINO, ASUNTO, TEXTO)
    assert dormidas == [2.0, 4.0, 5.0, 5.0]


def test_el_tope_de_espera_por_defecto_es_menor_que_el_plazo_total():
    c = ConfigEmail(api_key=KEY, remitente="a@agendemos.ar")
    assert c.tope_espera < c.timeout_total


async def test_el_mismo_cliente_http_sirve_para_varios_envios_y_se_cierra():
    c = _cliente(lambda r: httpx.Response(200))
    http = c._http
    await c.enviar(DESTINO, ASUNTO, TEXTO)
    await c.enviar(DESTINO, ASUNTO, TEXTO)
    assert c._http is http and not http.is_closed
    await c.cerrar()
    assert http.is_closed
