"""Envío con registro en notifications_log y reintentos 1s/5s/25s (§6.4)."""

import uuid

import httpx

from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    Boton,
    EnvioPedido,
    ErrorEnvio,
    TelegramAdapter,
    enviar_con_reintentos,
)
from platform_notifications.tests.conftest import cliente_con

CLIENTE = uuid.uuid4()


def pedido(texto="hola", **k):
    return EnvioPedido(end_customer_id=CLIENTE, destinatario="77", texto=texto, template="turno_aprobado", **k)


class Esperas:
    def __init__(self):
        self.segundos = []

    async def __call__(self, s):
        self.segundos.append(s)


def api(respuestas):
    cola = list(respuestas)

    def handler(req):
        return cola.pop(0)

    return handler


OK = httpx.Response(200, json={"ok": True, "result": {}})
FALLA = httpx.Response(500, json={"ok": False, "error_code": 500, "description": "Internal Server Error"})


def test_backoff_por_defecto_es_1_5_25():
    assert BACKOFF_POR_DEFECTO == (1, 5, 25)


async def test_exito_al_primer_intento_crea_pending_y_queda_sent(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([OK])) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    assert [x["status"] for x in registrador.llamadas] == ["pending", "sent"]
    assert registrador.llamadas[0]["id"] is None  # la fila se crea en el primer intento
    (fila,) = registrador.filas.values()
    assert (fila["status"], fila["retry_count"], fila["last_error"]) == ("sent", 0, None)
    assert fila["channel"] == "telegram" and fila["template"] == "turno_aprobado"
    assert esperas.segundos == []


async def test_falla_dos_veces_y_recupera_con_backoff_1_y_5(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([FALLA, FALLA, OK])) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    (fila,) = registrador.filas.values()
    assert fila["status"] == "sent" and fila["retry_count"] == 2
    assert esperas.segundos == [1, 5]


async def test_agota_3_reintentos_y_queda_failed(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([FALLA] * 4)) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    (fila,) = registrador.filas.values()
    assert fila["status"] == "failed" and fila["retry_count"] == 3
    assert fila["last_error"] == "500: Internal Server Error"
    assert esperas.segundos == [1, 5, 25]
    # los intermedios quedan 'pending' (con el último error) y solo el último es 'failed'
    assert [x["status"] for x in registrador.llamadas] == ["pending", "pending", "pending", "pending", "failed"]


async def test_backoff_configurable_a_cero(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([FALLA] * 4)) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, pedido(), backoff=(0, 0, 0), esperar=esperas
        )
    assert esperas.segundos == [0, 0, 0]


async def test_last_error_no_guarda_el_texto_enviado_ni_el_token(token, registrador):
    sensible = "Hola Ana, tu turno de depilación del 12/03 con DNI 30111222"

    def handler(req):
        # el proveedor (mal) devuelve el texto en su descripción: igual no debe quedar guardado
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": f"mal: {sensible} {token}"})

    async with cliente_con(handler) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, pedido(texto=sensible), backoff=(0, 0, 0), esperar=Esperas()
        )
    for llamada in registrador.llamadas:
        assert sensible not in (llamada["last_error"] or "")
        assert token not in (llamada["last_error"] or "")
    (fila,) = registrador.filas.values()
    assert fila["status"] == "failed" and fila["last_error"].startswith("400:")
    assert all(sensible not in str(v) for v in fila.values())


async def test_error_inesperado_del_adapter_tambien_se_registra_sin_detalle(token, registrador):
    class Roto(TelegramAdapter):
        async def send_message(self, destinatario, texto):
            raise RuntimeError(f"explotó con {texto} y {token}")

    await enviar_con_reintentos(
        Roto(token, None), registrador, pedido(texto="secreto"), backoff=(0,), esperar=Esperas()
    )
    (fila,) = registrador.filas.values()
    assert fila["status"] == "failed" and fila["last_error"] == "interno: RuntimeError"
    assert fila["retry_count"] == 1


async def test_con_botones_usa_send_buttons(token, registrador):
    usado = []

    class Espia(TelegramAdapter):
        async def send_buttons(self, destinatario, texto, botones):
            usado.append(botones)

    await enviar_con_reintentos(Espia(token, None), registrador, pedido(botones=(Boton("a", "b"),)), esperar=Esperas())
    assert usado == [(Boton("a", "b"),)]
    assert list(registrador.filas.values())[0]["status"] == "sent"


def test_error_envio_es_una_excepcion_con_codigo():
    e = ErrorEnvio("429", "Too Many Requests")
    assert e.codigo == "429" and str(e) == "429: Too Many Requests"


BLOQUEADO = httpx.Response(
    403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
)


def limite(segundos):
    return httpx.Response(
        429,
        json={
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
            "parameters": {"retry_after": segundos},
        },
    )


async def test_4xx_permanente_no_se_reintenta_y_queda_failed_de_una(token, registrador):
    esperas, intentos = Esperas(), []

    def handler(req):
        intentos.append(req)
        return BLOQUEADO

    async with cliente_con(handler) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    assert len(intentos) == 1
    (fila,) = registrador.filas.values()
    assert fila["status"] == "failed" and fila["retry_count"] == 0
    assert fila["last_error"] == "403: Forbidden: bot was blocked by the user"
    assert esperas.segundos == []
    assert [x["status"] for x in registrador.llamadas] == ["pending", "failed"]


async def test_429_espera_el_retry_after_de_telegram_y_recupera(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([limite(7), OK])) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    (fila,) = registrador.filas.values()
    assert fila["status"] == "sent" and fila["retry_count"] == 1
    assert esperas.segundos == [7]


async def test_429_con_retry_after_enorme_se_acota_al_tope(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([limite(3600), OK])) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, pedido(), esperar=esperas, tope_retry_after=30
        )
    assert esperas.segundos == [30]


async def test_429_sin_retry_after_usa_el_backoff(token, registrador):
    esperas = Esperas()
    sin = httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests"})
    async with cliente_con(api([sin, OK])) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    assert esperas.segundos == [1]


async def test_429_repetido_agota_los_reintentos(token, registrador):
    esperas = Esperas()
    async with cliente_con(api([limite(2)] * 4)) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    (fila,) = registrador.filas.values()
    assert fila["status"] == "failed" and fila["retry_count"] == 3
    assert esperas.segundos == [2, 2, 2]


async def test_timeout_y_5xx_siguen_con_el_backoff_fijo(token, registrador):
    esperas = Esperas()

    def handler(req):
        if not esperas.segundos:
            raise httpx.ReadTimeout("timeout " + str(req.url))
        return FALLA if len(esperas.segundos) == 1 else OK

    async with cliente_con(handler) as c:
        await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido(), esperar=esperas)
    (fila,) = registrador.filas.values()
    assert fila["status"] == "sent" and fila["retry_count"] == 2
    assert esperas.segundos == [1, 5]


def test_clasificacion_de_errores_de_envio():
    assert ErrorEnvio("403", "x", estado_http=403).reintentable is False
    assert ErrorEnvio("400", "x", estado_http=400).reintentable is False
    assert ErrorEnvio("429", "x", estado_http=429, retry_after=3).reintentable is True
    assert ErrorEnvio("500", "x", estado_http=500).reintentable is True
    assert ErrorEnvio("red", "ReadTimeout").reintentable is True
