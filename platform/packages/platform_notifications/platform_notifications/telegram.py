"""Adapter de Telegram sobre la Bot API (httpx). Canal de desarrollo/piloto (§2.6).

El token va en la URL (así lo exige Telegram), por eso: el error de red de httpx (que incluye la URL) se
descarta sin encadenar, el mensaje del proveedor se sanea y el `repr` del adapter no lo muestra.
"""

import os
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
from platform_secrets import Secreto

from platform_notifications.adapter import BotAdapter, Boton, MensajeEntrante
from platform_notifications.errores import ErrorEnvio

_API = "https://api.telegram.org"
VAR_BASE_URL = "TELEGRAM_API_BASE_URL"
_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "telegram-sim"}


def base_url_api(env: "dict[str, str] | os._Environ[str] | None" = None) -> str:
    """Base de la Bot API: la oficial, salvo que `TELEGRAM_API_BASE_URL` apunte a un simulador local.

    Solo se acepta `http://` hacia una máquina local (localhost, 127.0.0.1, host.docker.internal o el
    simulador del entorno local); cualquier otro valor (https ajeno, http a internet) se rechaza con un error
    claro: así una variable mal puesta en producción nunca manda el token a otro host.
    """
    valor = ((os.environ if env is None else env).get(VAR_BASE_URL) or "").strip().rstrip("/")
    if not valor:
        return _API
    partes = urlsplit(valor)
    if partes.scheme != "http" or partes.hostname not in _HOSTS_LOCALES or partes.username or partes.password:
        raise ValueError(f"{VAR_BASE_URL} solo admite http hacia una máquina local (localhost, 127.0.0.1)")
    return valor


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
        if any(b.pedir_contacto for b in botones):
            # El contacto solo se puede pedir con un teclado de respuesta (no inline); se oculta al usarlo.
            marcado: dict[str, Any] = {
                "keyboard": [[{"text": b.texto, "request_contact": True}] for b in botones if b.pedir_contacto],
                "one_time_keyboard": True,
                "resize_keyboard": True,
            }
        else:
            marcado = {"inline_keyboard": [[{"text": b.texto, "callback_data": b.dato}] for b in botones]}
        await self._llamar("sendMessage", {"chat_id": destinatario, "text": texto, "reply_markup": marcado})

    async def responder_callback(self, callback_id: str) -> None:
        await self._llamar("answerCallbackQuery", {"callback_query_id": callback_id})

    async def _llamar(self, metodo: str, cuerpo: dict[str, Any]) -> None:
        token = self._token.revelar()
        url = f"{base_url_api()}/bot{token}/{metodo}"
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
    def _es_privado(chat: Any) -> bool:
        """Solo se atienden chats privados: grupos, supergrupos y canales se descartan (el bot es 1 a 1)."""
        return not isinstance(chat, dict) or chat.get("type", "private") == "private"

    @staticmethod
    def parsear_update(payload: Any) -> MensajeEntrante | None:
        if not isinstance(payload, dict):
            return None
        update_id = str(payload.get("update_id", ""))
        mensaje = payload.get("message")
        if isinstance(mensaje, dict) and isinstance(mensaje.get("text"), str):
            chat, autor = mensaje.get("chat"), mensaje.get("from")
            if isinstance(chat, dict) and "id" in chat:
                if not TelegramAdapter._es_privado(chat):
                    return None
                chat_id = str(chat["id"])
                usuario = str(autor["id"]) if isinstance(autor, dict) and "id" in autor else chat_id
                return MensajeEntrante("telegram", chat_id, usuario, update_id, texto=mensaje["text"])
        if isinstance(mensaje, dict) and isinstance(mensaje.get("contact"), dict):
            chat, autor = mensaje.get("chat"), mensaje.get("from")
            if isinstance(chat, dict) and "id" in chat and isinstance(autor, dict) and "id" in autor:
                if not TelegramAdapter._es_privado(chat):
                    return None
                contacto = mensaje["contact"]
                telefono = contacto.get("phone_number")
                # Solo vale el contacto propio: Telegram informa el `user_id` del dueño del contacto, y un
                # contacto reenviado de otra persona (o sin dueño) no prueba que el número sea del remitente.
                propio = isinstance(telefono, str) and str(contacto.get("user_id")) == str(autor["id"])
                return MensajeEntrante(
                    "telegram",
                    str(chat["id"]),
                    str(autor["id"]),
                    update_id,
                    telefono_compartido=telefono if propio else None,
                )
        callback = payload.get("callback_query")
        if isinstance(callback, dict) and isinstance(callback.get("data"), str):
            origen_mensaje = callback.get("message")
            origen = origen_mensaje.get("chat") if isinstance(origen_mensaje, dict) else None
            if not TelegramAdapter._es_privado(origen):
                return None
            autor = callback.get("from")
            if isinstance(autor, dict) and "id" in autor:
                usuario = str(autor["id"])
                chat_id = str(origen["id"]) if isinstance(origen, dict) and "id" in origen else usuario
                return MensajeEntrante(
                    "telegram",
                    chat_id,
                    usuario,
                    update_id,
                    callback_data=callback["data"],
                    callback_id=str(callback["id"]) if "id" in callback else None,
                )
        return None
