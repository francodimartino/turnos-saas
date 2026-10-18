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
from platform_admin.errores import BotYaConectado, DatosInvalidos, IntegracionNoSoportada, TenantNoEncontrado

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
_UNIQUE_VIOLATION = "23505"
_RE_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{32,256}")


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


_PROFUNDIDAD_MAXIMA_CONFIG = 20


def _claves_de_credencial(valor: Any, nivel: int = 0) -> set[str]:
    if nivel > _PROFUNDIDAD_MAXIMA_CONFIG:
        raise DatosInvalidos(f"config demasiado anidado: máximo {_PROFUNDIDAD_MAXIMA_CONFIG} niveles")
    encontradas: set[str] = set()
    if isinstance(valor, dict):
        for clave, hijo in valor.items():
            if isinstance(clave, str) and clave.lower() in _CLAVES_DE_CREDENCIAL:
                encontradas.add(clave.lower())
            encontradas |= _claves_de_credencial(hijo, nivel + 1)
    elif isinstance(valor, (list, tuple)):
        for hijo in valor:
            encontradas |= _claves_de_credencial(hijo, nivel + 1)
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


def _validar_credencial_telegram(secret: Any) -> str:
    """El bot (`_credencial`) espera un JSON {bot_token, webhook_secret}; los mensajes no nombran el valor.

    El `webhook_secret` tiene de 32 a 256 caracteres [A-Za-z0-9_-]. Devuelve el `bot_id`: la parte numérica
    del `bot_token` (no secreta), que se guarda en `config` para garantizar su unicidad entre tenants.
    """
    valor = secret.revelar() if isinstance(secret, Secreto) else secret
    forma = (
        'el secret de telegram_bot debe ser un JSON objeto con "bot_token" (formato '
        '"<números>:<letras, números, _ o ->") y "webhook_secret" (32 a 256 caracteres de letras, números, _ o -)'
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
            "webhook_secret falta o no tiene el formato esperado: 32 a 256 caracteres de letras, números, _ o -"
        )
    return bot_token.split(":", 1)[0]


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
    Para `telegram_bot` escribe en `config` el `bot_id` (parte numérica del `bot_token`; un `bot_id` enviado por
    el cliente se pisa). El mismo bot en otro tenant se rechaza con `BotYaConectado`, sin identificar al otro
    negocio. El `webhook_secret` del bot debe tener de 32 a 256 caracteres.
    La auditoría la escribe `store_tenant_integration` en la misma transacción.
    """
    _validar_tipo(integration_type)
    configuracion = _validar_config(config)
    _validar_secret(secret)
    bot_id = None
    if integration_type == TIPO_TELEGRAM:
        bot_id = _validar_credencial_telegram(secret)
        configuracion["bot_id"] = bot_id

    try:
        return await _guardar(pool, tenant_id, integration_type, configuracion, secret, actor_id, bot_id)
    except Exception as exc:
        if bot_id is not None and getattr(exc, "sqlstate", None) == _UNIQUE_VIOLATION:
            raise BotYaConectado(_MENSAJE_BOT_YA_CONECTADO) from None
        raise


_MENSAJE_BOT_YA_CONECTADO = "Este bot de Telegram ya está conectado a otro negocio"


async def _guardar(
    pool: Any,
    tenant_id: Any,
    integration_type: str,
    configuracion: dict[str, Any],
    secret: "str | Secreto",
    actor_id: Any,
    bot_id: "str | None",
) -> ResultadoIntegracion:
    async with admin_tx(pool, actor_id) as conn:
        if await conn.fetchval("select id from public.tenants where id = $1", tenant_id) is None:
            raise TenantNoEncontrado("El tenant no existe")
        if bot_id is not None and await conn.fetchval(
            "select 1 from public.tenant_integrations "
            "where integration_type = $1 and config ->> 'bot_id' = $2 and tenant_id <> $3 limit 1",
            integration_type,
            bot_id,
            tenant_id,
        ):
            raise BotYaConectado(_MENSAJE_BOT_YA_CONECTADO)
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
