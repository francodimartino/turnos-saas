"""Los reintentos corren como BackgroundTasks de FastAPI: la respuesta HTTP no los espera (§6.4)."""

import asyncio
import uuid

import httpx
from fastapi import BackgroundTasks, FastAPI

from platform_notifications import EnvioPedido, TelegramAdapter, programar_envio
from platform_notifications.tests.conftest import cliente_con

FALLA = httpx.Response(500, json={"ok": False, "error_code": 500, "description": "boom"})


async def _llamar_asgi(app, ruta):
    """Invoca la app a nivel ASGI y avisa el instante en que el cuerpo de la respuesta ya salió."""
    respuesta_enviada = asyncio.Event()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": ruta,
        "raw_path": ruta.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("t", 1),
        "server": ("t", 80),
        "scheme": "http",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensaje):
        if mensaje["type"] == "http.response.body" and not mensaje.get("more_body"):
            respuesta_enviada.set()

    tarea = asyncio.create_task(app(scope, receive, send))
    return tarea, respuesta_enviada


async def test_la_respuesta_no_espera_los_reintentos(token, registrador):
    liberar = asyncio.Event()

    async def esperar(_segundos):  # el backoff queda bloqueado hasta que el test lo libere
        await liberar.wait()

    llamadas = []

    def handler(req):
        llamadas.append(1)
        return FALLA

    app = FastAPI()

    @app.post("/confirmar")
    async def confirmar(tareas: BackgroundTasks):
        cliente = cliente_con(handler)
        pedido = EnvioPedido(end_customer_id=uuid.uuid4(), destinatario="1", texto="hola", template="t")
        programar_envio(tareas, TelegramAdapter(token, cliente), registrador, pedido, esperar=esperar)
        return {"ok": True}

    tarea, enviada = await _llamar_asgi(app, "/confirmar")
    await asyncio.wait_for(enviada.wait(), timeout=5)
    await asyncio.sleep(0.05)
    # la respuesta ya salió y el envío sigue en pleno backoff: una fila 'pending', una sola llamada a Telegram
    assert not tarea.done()
    assert [x["status"] for x in registrador.llamadas] == ["pending", "pending"]
    assert len(llamadas) == 1
    liberar.set()
    await asyncio.wait_for(tarea, timeout=5)
    assert list(registrador.filas.values())[0]["status"] == "failed"
    assert len(llamadas) == 4
