"""Interfaz de adapter de bot (ARCHITECTURE.md §4.3, §2.6).

Cada canal (hoy Telegram, en dev/piloto; WhatsApp Business queda diferido) implementa esta interfaz; el resto
del sistema no sabe qué proveedor hay detrás. `destinatario` es el identificador del chat en ese canal
(`end_customers.channel_user_id`).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Boton:
    texto: str
    dato: str  # lo que vuelve en `MensajeEntrante.callback_data` al pulsarlo


@dataclass(frozen=True)
class MensajeEntrante:
    """Update entrante ya normalizado. El contenido es dato del cliente: no aparece en `repr` (ni en logs)."""

    canal: str
    chat_id: str
    usuario_id: str
    update_id: str
    texto: str | None = field(default=None, repr=False)
    callback_data: str | None = field(default=None, repr=False)


class BotAdapter(ABC):
    canal: str

    @abstractmethod
    async def send_message(self, destinatario: str, texto: str) -> None:
        """Envía un texto. Levanta `ErrorEnvio` (código/mensaje del proveedor) si falla."""

    @abstractmethod
    async def send_buttons(self, destinatario: str, texto: str, botones: Sequence[Boton]) -> None:
        """Envía un texto con botones de respuesta."""

    @staticmethod
    @abstractmethod
    def parsear_update(payload: Any) -> MensajeEntrante | None:
        """Normaliza el update entrante del proveedor; `None` si no es un mensaje ni un botón soportado."""
