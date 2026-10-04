"""TelegramAdapter con la API de Telegram simulada (httpx.MockTransport): sin red ni tokens reales."""

import json
import logging

import httpx
import pytest

from platform_notifications import (
    BotAdapter,
    Boton,
    ErrorEnvio,
    MensajeEntrante,
    TelegramAdapter,
)
from platform_notifications.tests.conftest import cliente_con


def test_telegram_cumple_la_interfaz_y_no_hay_whatsapp():
    assert issubclass(TelegramAdapter, BotAdapter)
    with pytest.raises(TypeError):
        BotAdapter()
    import platform_notifications

    assert not hasattr(platform_notifications, "WhatsAppAdapter")


async def test_send_message_usa_el_token_y_manda_chat_y_texto(token):
    vistos = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async with cliente_con(handler) as cliente:
        await TelegramAdapter(token, cliente).send_message("123", "hola")
    (req,) = vistos
    assert req.url.path == f"/bot{token}/sendMessage"
    assert json.loads(req.content) == {"chat_id": "123", "text": "hola"}


async def test_send_buttons_arma_teclado_inline(token):
    cuerpos = []

    def handler(req):
        cuerpos.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    botones = [Boton("Confirmar", "ok"), Boton("Cancelar", "no")]
    async with cliente_con(handler) as cliente:
        await TelegramAdapter(token, cliente).send_buttons("9", "Elegí", botones)
    assert cuerpos[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Confirmar", "callback_data": "ok"}],
            [{"text": "Cancelar", "callback_data": "no"}],
        ]
    }


async def test_error_de_la_api_deja_codigo_y_mensaje_del_proveedor(token):
    def handler(req):
        return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"})

    async with cliente_con(handler) as cliente:
        with pytest.raises(ErrorEnvio) as exc:
            await TelegramAdapter(token, cliente).send_message("1", "x")
    assert exc.value.codigo == "403"
    assert "bot was blocked" in str(exc.value)
    assert token not in str(exc.value)


async def test_error_de_red_no_filtra_el_token_ni_encadena_la_causa(token, caplog):
    def handler(req):
        raise httpx.ConnectError("no conecta a " + str(req.url))  # el mensaje de httpx incluye la URL con el token

    caplog.set_level(logging.DEBUG)
    async with cliente_con(handler) as cliente:
        with pytest.raises(ErrorEnvio) as exc:
            await TelegramAdapter(token, cliente).send_message("1", "x")
    assert exc.value.codigo == "red"
    assert token not in str(exc.value) and token not in repr(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert token not in caplog.text


async def test_el_mensaje_del_proveedor_se_sanea_si_trae_el_token(token):
    def handler(req):
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": f"raro {token} fin"})

    async with cliente_con(handler) as cliente:
        with pytest.raises(ErrorEnvio) as exc:
            await TelegramAdapter(token, cliente).send_message("1", "x")
    assert token not in str(exc.value)


async def test_respuesta_que_no_es_json_es_error_sin_cuerpo(token):
    def handler(req):
        return httpx.Response(502, text="<html>texto sensible</html>")

    async with cliente_con(handler) as cliente:
        with pytest.raises(ErrorEnvio) as exc:
            await TelegramAdapter(token, cliente).send_message("1", "x")
    assert exc.value.codigo == "502"
    assert "sensible" not in str(exc.value)


def test_el_repr_del_adapter_no_muestra_el_token(token):
    a = TelegramAdapter(token, None)
    assert token not in repr(a) and token not in str(a)


def test_parsea_mensaje_de_texto():
    update = {"update_id": 5, "message": {"message_id": 1, "chat": {"id": 42}, "from": {"id": 42}, "text": "hola"}}
    m = TelegramAdapter.parsear_update(update)
    assert m == MensajeEntrante(canal="telegram", chat_id="42", usuario_id="42", texto="hola", update_id="5")
    assert m.callback_data is None


def test_parsea_pulsacion_de_boton():
    update = {
        "update_id": 6,
        "callback_query": {"id": "c", "from": {"id": 42}, "data": "ok", "message": {"chat": {"id": 42}}},
    }
    m = TelegramAdapter.parsear_update(update)
    assert m.callback_data == "ok" and m.texto is None and m.chat_id == "42"


@pytest.mark.parametrize("update", [{}, {"update_id": 1}, {"update_id": 1, "message": {"chat": {"id": 1}}}, "x"])
def test_updates_no_soportados_devuelven_none(update):
    assert TelegramAdapter.parsear_update(update) is None


def test_el_repr_del_mensaje_entrante_no_muestra_el_texto():
    m = MensajeEntrante(canal="telegram", chat_id="1", usuario_id="1", texto="dato personal", update_id="1")
    assert "dato personal" not in repr(m)
