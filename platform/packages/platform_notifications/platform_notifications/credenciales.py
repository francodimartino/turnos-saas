"""Credencial del bot del tenant (§2.6): `find_integration` + `get_integration_secret` vía platform_secrets.

El secret de la integración `telegram_bot` es un JSON `{bot_token, webhook_secret}`. Se usa dentro de la
transacción con contexto de tenant (el almacén está ligado a esa conexión); el valor queda en `Secreto`.
"""

import json
from dataclasses import dataclass

import httpx
from platform_secrets import AlmacenSecretos, Secreto

from platform_notifications.errores import CredencialInvalida, IntegracionNoConfigurada
from platform_notifications.telegram import BASE_URL_OFICIAL, TelegramAdapter

TIPO_INTEGRACION_TELEGRAM = "telegram_bot"


@dataclass(frozen=True)
class CredencialTelegram:
    bot_token: Secreto
    webhook_secret: Secreto | None


def _parsear(valor: str) -> dict | None:
    try:
        datos = json.loads(valor)
    except ValueError:
        return None
    return datos if isinstance(datos, dict) else None


async def leer_credencial_telegram(almacen: AlmacenSecretos) -> CredencialTelegram:
    integracion = await almacen.buscar(TIPO_INTEGRACION_TELEGRAM)
    if integracion is None:
        raise IntegracionNoConfigurada("el tenant no tiene integración de Telegram")
    secreto = await almacen.obtener(integracion)
    if secreto is None:
        raise IntegracionNoConfigurada("la integración de Telegram no tiene secret")
    datos = _parsear(secreto.revelar())
    if datos is None:
        raise CredencialInvalida("el secret de Telegram no es un objeto JSON")
    token, webhook = datos.get("bot_token"), datos.get("webhook_secret")
    if not isinstance(token, str) or token == "":
        raise CredencialInvalida("el secret de Telegram no tiene bot_token")
    return CredencialTelegram(
        bot_token=Secreto(token),
        webhook_secret=Secreto(webhook) if isinstance(webhook, str) and webhook else None,
    )


async def adapter_telegram_del_tenant(
    almacen: AlmacenSecretos, cliente: httpx.AsyncClient | None = None, *, base_url: str = BASE_URL_OFICIAL
) -> TelegramAdapter:
    """Adapter con el bot del tenant del contexto de la transacción."""
    credencial = await leer_credencial_telegram(almacen)
    return TelegramAdapter(credencial.bot_token, cliente, base_url=base_url)
