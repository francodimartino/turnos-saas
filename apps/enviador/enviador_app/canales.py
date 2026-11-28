"""Canales de envío del enviador: cada uno es un `BotAdapter`, así todos usan la misma política de reintentos.

Telegram usa el adapter de `platform_notifications` con el token del negocio. El email usa `turnos_email`
(Resend por HTTP) detrás de `AdaptadorEmail`, que traduce su error al `ErrorEnvio` que entiende la política: un 4xx
distinto de 429 es permanente; red, timeout, 429 y 5xx se reintentan (se prefiere un posible duplicado a perder el
aviso, igual que con Telegram).
"""

from collections.abc import Sequence
from typing import Any, Protocol

from platform_notifications import BotAdapter, Boton, ErrorEnvio
from turnos_email import ErrorEmail

CANAL_TELEGRAM = "telegram"
CANAL_EMAIL = "email"
CANALES_ENVIABLES = (CANAL_TELEGRAM, CANAL_EMAIL)


class ClienteDeEmail(Protocol):
    """Lo que el enviador necesita de `turnos_email.ClienteEmail` (y lo que reemplazan los tests)."""

    async def enviar(self, destino: str, asunto: str, texto: str, html: str | None = None) -> None: ...


class AdaptadorEmail(BotAdapter):
    """Un email de un solo asunto. `destinatario` es la dirección; `texto` el cuerpo en texto plano."""

    canal = CANAL_EMAIL

    def __init__(self, cliente: ClienteDeEmail, asunto: str) -> None:
        self._cliente = cliente
        self._asunto = asunto

    async def send_message(self, destinatario: str, texto: str) -> None:
        try:
            await self._cliente.enviar(destinatario, self._asunto, texto)
        except ErrorEmail as error:
            # El mensaje de `ErrorEmail` es fijo (estado HTTP o "sin respuesta"): no lleva destino ni cuerpo.
            raise ErrorEnvio(
                "email", str(error), estado_http=error.estado_http, retry_after=error.retry_after
            ) from None

    async def send_buttons(self, destinatario: str, texto: str, botones: Sequence[Boton]) -> None:
        """El email no tiene botones de respuesta: manda solo el texto."""
        await self.send_message(destinatario, texto)

    @staticmethod
    def parsear_update(payload: Any):
        """El email no recibe mensajes por este canal."""
        return
