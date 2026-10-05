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
    # Botón de "compartir mi contacto": el canal devuelve el teléfono verificado del propio usuario
    # (`MensajeEntrante.telefono_compartido`). En Telegram es un teclado de respuesta, no uno inline.
    pedir_contacto: bool = False


@dataclass(frozen=True)
class MensajeEntrante:
    """Update entrante ya normalizado. El contenido es dato del cliente: no aparece en `repr` (ni en logs)."""

    canal: str
    chat_id: str
    usuario_id: str
    update_id: str
    texto: str | None = field(default=None, repr=False)
    callback_data: str | None = field(default=None, repr=False)
    # Teléfono que el propio usuario compartió con el botón de contacto (solo si el canal garantiza que es suyo).
    telefono_compartido: str | None = field(default=None, repr=False)
    # Id de la pulsación de un botón: hay que contestarlo (`responder_callback`) para que el canal quite el "cargando".
    callback_id: str | None = field(default=None, repr=False)


class BotAdapter(ABC):
    canal: str

    @abstractmethod
    async def send_message(self, destinatario: str, texto: str) -> None:
        """Envía un texto. Levanta `ErrorEnvio` (código/mensaje del proveedor) si falla."""

    @abstractmethod
    async def send_buttons(self, destinatario: str, texto: str, botones: Sequence[Boton]) -> None:
        """Envía un texto con botones de respuesta."""

    async def responder_callback(self, callback_id: str) -> None:
        """Confirma al canal que se recibió la pulsación de un botón. Por defecto no hace nada."""
        return None

    @staticmethod
    @abstractmethod
    def parsear_update(payload: Any) -> MensajeEntrante | None:
        """Normaliza el update entrante del proveedor; `None` si no es un mensaje ni un botón soportado."""
