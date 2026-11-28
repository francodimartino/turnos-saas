"""Un adapter que recuerda el último `ErrorEnvio` (para distinguir un fallo permanente del proveedor) (§6.4)."""

from collections.abc import Awaitable, Sequence
from typing import Any

from platform_notifications.adapter import BotAdapter, Boton
from platform_notifications.errores import ErrorEnvio


class CanalVigilado(BotAdapter):
    """Delega en el adapter real y guarda el último `ErrorEnvio` en `ultimo_error` (p. ej. un 401/403 del proveedor
    es una credencial caída o un destinatario que bloqueó al bot, y reintentar no lo arregla)."""

    def __init__(self, real: BotAdapter) -> None:
        self._real = real
        self.canal = real.canal
        self.ultimo_error: ErrorEnvio | None = None

    async def _vigilar(self, llamada: Awaitable[None]) -> None:
        try:
            await llamada
        except ErrorEnvio as error:
            self.ultimo_error = error
            raise

    async def send_message(self, destinatario: str, texto: str) -> None:
        await self._vigilar(self._real.send_message(destinatario, texto))

    async def send_buttons(self, destinatario: str, texto: str, botones: Sequence[Boton]) -> None:
        await self._vigilar(self._real.send_buttons(destinatario, texto, botones))

    @staticmethod
    def parsear_update(payload: Any):
        """Un adapter vigilado solo envía: no recibe updates."""
        return
