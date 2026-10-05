"""Contacto compartido, botón de pedir contacto y answerCallbackQuery del TelegramAdapter (tarea 34)."""

import json

import httpx
import pytest

from platform_notifications import Boton, ErrorEnvio, MensajeEntrante, TelegramAdapter
from platform_notifications.tests.conftest import cliente_con

# Número de ejemplo inventado (formato AR), no es de una persona real.
TEL = "5491100000000"


def _contacto(user_id, remitente=42, phone=TEL):
    contacto = {"phone_number": phone, "first_name": "X"}
    if user_id is not None:
        contacto["user_id"] = user_id
    return {
        "update_id": 9,
        "message": {"message_id": 1, "chat": {"id": remitente}, "from": {"id": remitente}, "contact": contacto},
    }


def test_contacto_propio_se_expone_como_telefono_compartido():
    m = TelegramAdapter.parsear_update(_contacto(user_id=42))
    assert m is not None
    assert m.telefono_compartido == TEL and m.texto is None and m.usuario_id == "42"


@pytest.mark.parametrize("user_id", [7, None])
def test_contacto_de_otra_persona_o_sin_dueno_no_se_acepta(user_id):
    m = TelegramAdapter.parsear_update(_contacto(user_id=user_id))
    assert m is not None  # el mensaje existe (para poder volver a pedir), pero sin teléfono
    assert m.telefono_compartido is None


def test_el_repr_no_muestra_telefono_ni_id_de_callback():
    m = TelegramAdapter.parsear_update(_contacto(user_id=42))
    assert TEL not in repr(m)


def test_el_callback_conserva_su_id():
    update = {
        "update_id": 6,
        "callback_query": {"id": "cb-1", "from": {"id": 42}, "data": "ok", "message": {"chat": {"id": 42}}},
    }
    assert TelegramAdapter.parsear_update(update).callback_id == "cb-1"


async def test_boton_de_pedir_contacto_arma_teclado_de_respuesta(token):
    cuerpos = []

    def handler(req):
        cuerpos.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with cliente_con(handler) as cliente:
        await TelegramAdapter(token, cliente).send_buttons(
            "9", "Compartí tu contacto", [Boton("Compartir mi contacto", "", pedir_contacto=True)]
        )
    assert cuerpos[0]["reply_markup"] == {
        "keyboard": [[{"text": "Compartir mi contacto", "request_contact": True}]],
        "one_time_keyboard": True,
        "resize_keyboard": True,
    }


async def test_responder_callback_llama_a_answer_callback_query(token):
    vistos = []

    def handler(req):
        vistos.append(req)
        return httpx.Response(200, json={"ok": True, "result": True})

    async with cliente_con(handler) as cliente:
        await TelegramAdapter(token, cliente).responder_callback("cb-1")
    (req,) = vistos
    assert req.url.path == f"/bot{token}/answerCallbackQuery"
    assert json.loads(req.content) == {"callback_query_id": "cb-1"}


async def test_responder_callback_con_error_de_la_api_levanta_error_envio(token):
    def handler(req):
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "query is too old"})

    async with cliente_con(handler) as cliente:
        with pytest.raises(ErrorEnvio):
            await TelegramAdapter(token, cliente).responder_callback("cb-1")


def test_mensaje_entrante_sigue_aceptando_la_forma_anterior():
    m = MensajeEntrante(canal="telegram", chat_id="1", usuario_id="1", update_id="1", texto="hola")
    assert m.telefono_compartido is None and m.callback_id is None
