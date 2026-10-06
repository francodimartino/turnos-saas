"""Carga de credenciales de integraciones a nivel tenant (ARCHITECTURE.md §2.6, §2.11.5).

Franco carga la credencial del bot del tenant desde el panel. Solo `telegram_bot` entra en el piloto: las
integraciones por-profesional (Google Calendar) las conecta cada profesional por OAuth desde su dashboard, y
`whatsapp_business` queda fuera hasta la salida a producción con el BSP.

La credencial viaja solo hasta `store_tenant_integration` (Vault, vía `platform_secrets`): este módulo no la
devuelve, no la loguea ni la incluye en ningún mensaje de error. `config` es para datos NO secretos; se rechaza
si trae claves de credencial, porque `tenant_integrations.config` es una tabla plana.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from platform_secrets import Secreto, crear_almacen
from platform_tenancy import admin_tx

from platform_admin._json import decodificar
from platform_admin.errores import DatosInvalidos, IntegracionNoSoportada, TenantNoEncontrado

TIPO_TELEGRAM = "telegram_bot"
_POR_PROFESIONAL = frozenset({"google_calendar"})
_CLAVES_DE_CREDENCIAL = frozenset(
    {
        "bot_token",
        "webhook_secret",
        "token",
        "secret",
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
    }
)
_RE_BOT_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")
_RE_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{1,256}")


@dataclass(frozen=True)
class ResultadoIntegracion:
    """Resultado de la carga. Sin credencial: `webhook_key` es un identificador no secreto (§2.6)."""

    integration_id: uuid.UUID
    tenant_id: uuid.UUID
    integration_type: str
    webhook_key: str


def _validar_tipo(integration_type: Any) -> None:
    if integration_type == TIPO_TELEGRAM:
        return
    if integration_type in _POR_PROFESIONAL:
        raise IntegracionNoSoportada(
            "google_calendar es una integración por-profesional: la conecta cada profesional por OAuth "
            "desde su dashboard, no se carga desde el panel admin"
        )
    if integration_type == "whatsapp_business":
        raise IntegracionNoSoportada("whatsapp_business está fuera del piloto: solo se carga telegram_bot")
    raise IntegracionNoSoportada("este tipo de integración no se carga desde el panel: solo telegram_bot")


def _claves_de_credencial(valor: Any) -> set[str]:
    encontradas: set[str] = set()
    if isinstance(valor, dict):
        for clave, hijo in valor.items():
            if isinstance(clave, str) and clave.lower() in _CLAVES_DE_CREDENCIAL:
                encontradas.add(clave.lower())
            encontradas |= _claves_de_credencial(hijo)
    elif isinstance(valor, (list, tuple)):
        for hijo in valor:
            encontradas |= _claves_de_credencial(hijo)
    return encontradas


def _como_uuid_texto(valor: Any) -> str:
    try:
        if isinstance(valor, uuid.UUID):
            return str(valor)
        if isinstance(valor, str):
            return str(uuid.UUID(valor))
    except ValueError:
        pass
    raise DatosInvalidos("config.webhook_key debe ser un UUID")


def _validar_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise DatosInvalidos("config debe ser un objeto")
    prohibidas = _claves_de_credencial(config)
    if prohibidas:
        raise DatosInvalidos(
            f"config es para datos no secretos; las credenciales van en secret. Claves: {sorted(prohibidas)}"
        )
    limpia = dict(config)
    if "webhook_key" in limpia:
        limpia["webhook_key"] = _como_uuid_texto(limpia["webhook_key"])
    return limpia


def _validar_secret(secret: Any) -> None:
    valor = secret.revelar() if isinstance(secret, Secreto) else secret
    if not isinstance(valor, str) or valor == "":
        raise DatosInvalidos("secret es obligatorio y debe ser texto")


def _validar_credencial_telegram(secret: Any) -> None:
    """El bot (`_credencial`) espera un JSON {bot_token, webhook_secret}; los mensajes no nombran el valor."""
    valor = secret.revelar() if isinstance(secret, Secreto) else secret
    forma = (
        'el secret de telegram_bot debe ser un JSON objeto con "bot_token" (formato '
        '"<números>:<letras, números, _ o ->") y "webhook_secret" (1 a 256 caracteres de letras, números, _ o -)'
    )
    try:
        datos = json.loads(valor)
    except (ValueError, RecursionError):  # RecursionError: JSON con anidamiento extremo
        raise DatosInvalidos(forma) from None
    if not isinstance(datos, dict):
        raise DatosInvalidos(forma)
    bot_token, webhook_secret = datos.get("bot_token"), datos.get("webhook_secret")
    if not isinstance(bot_token, str) or _RE_BOT_TOKEN.fullmatch(bot_token) is None:
        raise DatosInvalidos('bot_token falta o no tiene el formato esperado: "<números>:<letras, números, _ o ->"')
    if not isinstance(webhook_secret, str) or _RE_WEBHOOK_SECRET.fullmatch(webhook_secret) is None:
        raise DatosInvalidos(
            "webhook_secret falta o no tiene el formato esperado: 1 a 256 caracteres de letras, números, _ o -"
        )


async def cargar_integracion_tenant(
    pool: Any,
    tenant_id: Any,
    integration_type: str,
    config: dict[str, Any],
    secret: "str | Secreto",
    *,
    actor_id: Any,
) -> ResultadoIntegracion:
    """Guarda la credencial (cifrada en Vault) y la config no secreta de la integración del tenant.

    Solo `telegram_bot`. Si `config` no trae `webhook_key` se genera un UUID aleatorio; si la integración ya
    existía se conserva la suya (regenerarla rompería el webhook ya registrado en Telegram con `setWebhook`).
    La auditoría la escribe `store_tenant_integration` en la misma transacción.
    """
    _validar_tipo(integration_type)
    configuracion = _validar_config(config)
    _validar_secret(secret)
    if integration_type == TIPO_TELEGRAM:
        _validar_credencial_telegram(secret)

    async with admin_tx(pool, actor_id) as conn:
        if await conn.fetchval("select id from public.tenants where id = $1", tenant_id) is None:
            raise TenantNoEncontrado("El tenant no existe")
        if "webhook_key" not in configuracion:
            existente = await conn.fetchval(
                "select config from public.tenant_integrations "
                "where tenant_id = $1 and integration_type = $2 and professional_id is null",
                tenant_id,
                integration_type,
            )
            previa = (decodificar(existente) or {}).get("webhook_key")
            configuracion["webhook_key"] = previa or str(uuid.uuid4())
        integration_id = await crear_almacen(conn).guardar(tenant_id, integration_type, configuracion, secret)
    return ResultadoIntegracion(
        integration_id=integration_id,
        tenant_id=tenant_id,
        integration_type=integration_type,
        webhook_key=configuracion["webhook_key"],
    )
