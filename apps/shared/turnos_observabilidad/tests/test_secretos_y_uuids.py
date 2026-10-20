"""Sin credenciales en logs/Sentry y sin tocar uuids en el filtro de teléfonos."""

import io
import json
import logging

import httpx

from turnos_observabilidad import configurar_logging, sentry_before_send
from turnos_observabilidad.pii import limpiar_texto

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
UUIDS_SOLO_DIGITOS = [
    "12345678-1234-1234-1234-123456789012",
    "00000000-0000-0000-0000-000000000000",
    "11223344-5566-7788-9900-112233445566",
    # Casos que fallaban de forma intermitente con uuid4 aleatorios.
    "7c847362-4286-4105-8828-5c5e2f0aee2e",
    "34548756-8425-4db0-bbc8-e4f2879274a8",
    "72652452-7927-4d8f-b8bf-7ccff7e3490e",
    "ABCDEF12-1234-5678-9012-ABCDEF123456",
]


def _cliente_simulado() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))


def test_httpx_en_debug_no_filtra_el_token_del_bot():
    buf = io.StringIO()
    configurar_logging(nivel=logging.DEBUG, stream=buf)
    try:
        with _cliente_simulado() as c:
            c.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": 1})
        # Aunque alguien suba httpx a DEBUG a mano, el formatter redacta la URL.
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        with _cliente_simulado() as c:
            c.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": 1})
    finally:
        logging.getLogger("httpx").setLevel(logging.NOTSET)
    salida = buf.getvalue()
    assert "AAHdqTcv" not in salida
    assert TOKEN not in salida
    assert TOKEN.split(":")[0] not in salida


def test_httpx_y_httpcore_quedan_en_warning_por_defecto():
    configurar_logging(stream=io.StringIO())
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_sentry_redacta_token_en_breadcrumbs_y_mensajes():
    evento = {
        "message": f"fallo en https://api.telegram.org/bot{TOKEN}/sendMessage",
        "breadcrumbs": {
            "values": [
                {
                    "category": "httpx",
                    "data": {"url": f"https://api.telegram.org/bot{TOKEN}/getMe", "method": "GET"},
                    "message": f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/sendMessage "HTTP/1.1 200 OK"',
                }
            ]
        },
    }
    assert TOKEN not in json.dumps(sentry_before_send(evento))
    assert "AAHdqTcv" not in json.dumps(sentry_before_send(evento))


def test_redacta_querystrings_y_cuerpos_de_oauth():
    texto = (
        "https://oauth2.googleapis.com/token?code=4/0AbCd&client_secret=GOCSPX-xyz"
        "&refresh_token=1//0gREF&access_token=ya29.abc&status_code=200"
    )
    out = limpiar_texto(texto)
    for secreto in ("4/0AbCd", "GOCSPX-xyz", "1//0gREF", "ya29.abc"):
        assert secreto not in out
    assert "status_code=200" in out
    assert "refresh_token" in out
    assert "ya29" not in limpiar_texto('{"access_token": "ya29.zzz", "expires_in": 3599}')
    assert "ya29" not in limpiar_texto("Authorization: Bearer ya29.zzz")


def test_url_normal_no_se_altera():
    assert limpiar_texto("GET /bottles/12 y /health") == "GET /bottles/12 y /health"


def test_pii_no_altera_uuids_con_grupos_numericos():
    for u in UUIDS_SOLO_DIGITOS:
        assert limpiar_texto(f"tenant {u} ok") == f"tenant {u} ok"
        assert limpiar_texto(f"/api/{u}/turnos") == f"/api/{u}/turnos"
        assert limpiar_texto(f'{{"id": "{u}"}}') == f'{{"id": "{u}"}}'


def test_pii_sigue_filtrando_telefono_junto_a_uuid():
    out = limpiar_texto(f"{UUIDS_SOLO_DIGITOS[0]} llamó al +54 9 11 5555-0000")
    assert UUIDS_SOLO_DIGITOS[0] in out
    assert "5555" not in out
