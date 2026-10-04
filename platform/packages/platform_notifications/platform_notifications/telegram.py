"""Adapter de Telegram sobre la Bot API (httpx). Canal de desarrollo/piloto (§2.6).

El token va en la URL (así lo exige Telegram), por eso: el error de red de httpx (que incluye la URL) se
descarta sin encadenar, el mensaje del proveedor se sanea y el `repr` del adapter no lo muestra.
"""

from collections.abc import Sequence
from typing import Any

import httpx
from platform_secrets import Secreto

from platform_notifications.adapter import BotAdapter, Boton, MensajeEntrante
from platform_notifications.errores import ErrorEnvio

_API = "https://api.telegram.org"
_TIMEOUT = httpx.Timeout(10.0)
_MAX_MENSAJE = 300


class TelegramAdapter(BotAdapter):
    canal = "telegram"

    def __init__(self, bot_token: "str | Secreto", cliente: httpx.AsyncClient | None = None) -> None:
        self._token = bot_token if isinstance(bot_token, Secreto) else Secreto(bot_token)
        self._cliente = cliente  # inyectable (tests); sin él se abre uno por envío

    def __repr__(self) -> str:
        return "TelegramAdapter(bot_token=***)"

    __str__ = __repr__

    async def send_message(self, destinatario: str, texto: str) -> None:
        await self._llamar("sendMessage", {"chat_id": destinatario, "text": texto})

    async def send_buttons(self, destinatario: str, texto: str, botones: Sequence[Boton]) -> None:
        teclado = [[{"text": b.texto, "callback_data": b.dato}] for b in botones]
        await self._llamar(
            "sendMessage",
            {"chat_id": destinatario, "text": texto, "reply_markup": {"inline_keyboard": teclado}},
        )

    async def _llamar(self, metodo: str, cuerpo: dict[str, Any]) -> None:
        token = self._token.revelar()
        url = f"{_API}/bot{token}/{metodo}"
        try:
            if self._cliente is not None:
                respuesta = await self._cliente.post(url, json=cuerpo)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as cliente:
                    respuesta = await cliente.post(url, json=cuerpo)
        except httpx.HTTPError as error:
            # El mensaje de httpx puede traer la URL (con el token): solo se conserva el tipo.
            raise ErrorEnvio("red", type(error).__name__) from None
        if respuesta.status_code == 200:
            return
        raise self._error(respuesta, token)

    @staticmethod
    def _error(respuesta: httpx.Response, token: str) -> ErrorEnvio:
        codigo, descripcion = str(respuesta.status_code), "sin detalle"
        try:
            datos = respuesta.json()
        except ValueError:
            datos = None
        if isinstance(datos, dict):
            codigo = str(datos.get("error_code") or codigo)
            if isinstance(datos.get("description"), str):
                descripcion = datos["description"]
        descripcion = descripcion.replace(token, "[token omitido]")[:_MAX_MENSAJE]
        return ErrorEnvio(codigo, descripcion)

    @staticmethod
    def parsear_update(payload: Any) -> MensajeEntrante | None:
        if not isinstance(payload, dict):
            return None
        update_id = str(payload.get("update_id", ""))
        mensaje = payload.get("message")
        if isinstance(mensaje, dict) and isinstance(mensaje.get("text"), str):
            chat, autor = mensaje.get("chat"), mensaje.get("from")
            if isinstance(chat, dict) and "id" in chat:
                chat_id = str(chat["id"])
                usuario = str(autor["id"]) if isinstance(autor, dict) and "id" in autor else chat_id
                return MensajeEntrante("telegram", chat_id, usuario, update_id, texto=mensaje["text"])
        callback = payload.get("callback_query")
        if isinstance(callback, dict) and isinstance(callback.get("data"), str):
            origen = (callback.get("message") or {}).get("chat")
            autor = callback.get("from")
            if isinstance(autor, dict) and "id" in autor:
                usuario = str(autor["id"])
                chat_id = str(origen["id"]) if isinstance(origen, dict) and "id" in origen else usuario
                return MensajeEntrante("telegram", chat_id, usuario, update_id, callback_data=callback["data"])
        return None
