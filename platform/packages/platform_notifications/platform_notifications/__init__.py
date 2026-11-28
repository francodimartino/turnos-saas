"""platform_notifications — adapters de bot y envío con reintentos (ARCHITECTURE.md §4.3, §6.4).

Las apps importan solo desde acá. Telegram es el canal de desarrollo/piloto; WhatsApp Business (producción)
queda diferido: se suma como otra implementación de `BotAdapter` sin tocar el resto.
"""

from platform_notifications.adapter import BotAdapter, Boton, MensajeEntrante
from platform_notifications.credenciales import (
    CredencialTelegram,
    adapter_telegram_del_tenant,
    leer_credencial_telegram,
)
from platform_notifications.envio import (
    BACKOFF_POR_DEFECTO,
    TOPE_RETRY_AFTER_POR_DEFECTO,
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
from platform_notifications.outbox import (
    CompletadorOutbox,
    CompletadorOutboxFuncion,
    completar_sin_lanzar,
    encolar_aviso,
    encolar_aviso_al_negocio,
)
from platform_notifications.registro import ObservadorDeIntentos, RegistradorFuncion, RegistradorIntentos, SinRegistro
from platform_notifications.telegram import BASE_URL_OFICIAL, TelegramAdapter, base_url_api
from platform_notifications.vigilancia import CanalVigilado

__all__ = [
    "BASE_URL_OFICIAL",
    "CanalVigilado",
    "BACKOFF_POR_DEFECTO",
    "TOPE_RETRY_AFTER_POR_DEFECTO",
    "BotAdapter",
    "Boton",
    "CompletadorOutbox",
    "CompletadorOutboxFuncion",
    "CredencialInvalida",
    "CredencialTelegram",
    "ErrorCredencial",
    "ErrorEnvio",
    "EnvioPedido",
    "IntegracionNoConfigurada",
    "MensajeEntrante",
    "ObservadorDeIntentos",
    "RegistradorFuncion",
    "RegistradorIntentos",
    "SinRegistro",
    "TelegramAdapter",
    "adapter_telegram_del_tenant",
    "base_url_api",
    "completar_sin_lanzar",
    "encolar_aviso",
    "encolar_aviso_al_negocio",
    "enviar_con_reintentos",
    "leer_credencial_telegram",
    "programar_envio",
]
