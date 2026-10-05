"""Webhook con la cadena de producción: handler de identidad, acciones posteriores al commit y callbacks."""

import httpx

from bot_app import ContextoMensaje, Despachador, crear_app
from bot_app.tests.conftest import TelegramFalso, update

CABECERA = "X-Telegram-Bot-Api-Secret-Token"
RUTA = "/api/bot/telegram/clave-no-secreta"


def montar(pool, despachador=None):
    telegram = TelegramFalso(pool)
    app = crear_app(pool=pool, despachador=despachador, cliente_http=telegram.cliente())
    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transporte, base_url="http://bot"), telegram


async def enviar(cliente, datos_bot, payload):
    return await cliente.post(RUTA, json=payload, headers={CABECERA: datos_bot[1]})


def _callback(update_id=1, dato="x", usuario=111):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": usuario},
            "data": dato,
            "message": {"message_id": 1, "chat": {"id": usuario}},
        },
    }


async def test_la_cadena_de_produccion_arranca_con_identidad_y_pide_el_contacto(pool, datos_bot):
    cliente, telegram = montar(pool)  # sin despachador propio: handlers por defecto
    r = await enviar(cliente, datos_bot, update(texto="hola"))
    assert r.status_code == 200
    (llamada,) = telegram.llamadas
    assert llamada["url"].endswith("/sendMessage")
    teclado = llamada["cuerpo"]["reply_markup"]["keyboard"]
    assert teclado == [[{"text": "Compartir mi contacto", "request_contact": True}]]
    assert pool.sesiones["111"]["identidad"]["paso"] == "telefono"


async def test_la_accion_posterior_corre_despues_del_commit_con_los_recursos(pool, datos_bot):
    corridas = []

    async def handler(ctx: ContextoMensaje) -> bool:
        async def accion(recursos):
            corridas.append((list(pool.eventos), recursos.pool))

        ctx.despues_del_commit(accion)
        return True

    cliente, _ = montar(pool, Despachador([handler]))
    await enviar(cliente, datos_bot, update())
    ((eventos, recibido),) = corridas
    assert eventos[-1] == "commit" and recibido is pool


async def test_si_la_transaccion_se_revierte_la_accion_posterior_no_corre(pool, datos_bot):
    corridas = []

    async def handler(ctx):
        ctx.despues_del_commit(lambda recursos: corridas.append(1))
        raise RuntimeError("falla simulada")

    cliente, _ = montar(pool, Despachador([handler]))
    r = await enviar(cliente, datos_bot, update())
    assert r.status_code == 500 and corridas == []


async def test_una_accion_posterior_que_falla_no_cambia_el_200_ni_frena_a_las_demas(pool, datos_bot):
    corridas = []

    async def mala(recursos):
        raise RuntimeError("falla simulada")

    async def buena(recursos):
        corridas.append(1)

    async def handler(ctx):
        ctx.despues_del_commit(mala)
        ctx.despues_del_commit(buena)
        return True

    cliente, _ = montar(pool, Despachador([handler]))
    r = await enviar(cliente, datos_bot, update())
    assert r.status_code == 200 and corridas == [1]


async def test_toda_pulsacion_de_boton_se_confirma_con_answer_callback_query(pool, datos_bot):
    async def handler(ctx):
        return True

    cliente, telegram = montar(pool, Despachador([handler]))
    await enviar(cliente, datos_bot, _callback())
    (llamada,) = telegram.llamadas
    assert llamada["url"].endswith("/answerCallbackQuery")
    assert llamada["cuerpo"] == {"callback_query_id": "cb-1"}
