"""platform_notifications — adapters de bot y envío con reintentos (ARCHITECTURE.md §4.3, §6.4).

Las apps importan solo desde acá. Telegram es el canal de desarrollo/piloto; WhatsApp Business (producción)
queda diferido: se suma como otra implementación de `BotAdapter` sin tocar el resto.
"""

from platform_notifications.adapter import BotAdapter, Boton, MensajeEntrante
from platform_notifications.avisos import PLANTILLA_VENCIMIENTO, ZONA_NEGOCIO, mensaje_vencimiento
from platform_notifications.credenciales import (
    CredencialTelegram,
    adapter_telegram_del_tenant,
    leer_credencial_telegram,
)
from platform_notifications.envio import (
    BACKOFF_POR_DEFECTO,
    EnvioPedido,
    enviar_con_reintentos,
    programar_envio,
)
from platform_notifications.errores import (
    CredencialInvalida,
    ErrorCredencial,
    ErrorEnvio,
    IntegracionNoConfigurada,
)
from platform_notifications.registro import RegistradorFuncion, RegistradorIntentos
from platform_notifications.telegram import TelegramAdapter

__all__ = [
    "BACKOFF_POR_DEFECTO",
    "BotAdapter",
    "Boton",
    "CredencialInvalida",
    "CredencialTelegram",
    "ErrorCredencial",
    "ErrorEnvio",
    "EnvioPedido",
    "IntegracionNoConfigurada",
    "MensajeEntrante",
    "PLANTILLA_VENCIMIENTO",
    "RegistradorFuncion",
    "RegistradorIntentos",
    "TelegramAdapter",
    "ZONA_NEGOCIO",
    "adapter_telegram_del_tenant",
    "enviar_con_reintentos",
    "leer_credencial_telegram",
    "mensaje_vencimiento",
    "programar_envio",
]
