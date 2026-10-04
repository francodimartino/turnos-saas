"""Configuración del bot desde el entorno. El bot corre siempre con el rol `bot_service` (§3.2)."""

import os
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

ROL_BOT = "bot_service"
VARIABLE_DSN = "BOT_DATABASE_URL"
VARIABLE_SENTRY = "SENTRY_DSN"
SERVICIO = "turnos-bot"


class ConfiguracionInvalida(RuntimeError):
    """Falta una variable obligatoria o la conexión no es del rol `bot_service`."""


def usuario_del_dsn(dsn: str) -> str | None:
    usuario = urlsplit(dsn).username
    return None if usuario is None else unquote(usuario)


def validar_dsn_bot(dsn: str) -> str:
    """Rechaza un DSN que no conecte como `bot_service` (nunca como superusuario ni como otro rol)."""
    if usuario_del_dsn(dsn) != ROL_BOT:
        raise ConfiguracionInvalida(f"{VARIABLE_DSN} debe conectar con el rol {ROL_BOT}")
    return dsn


def dsn_desde_entorno(entorno: Mapping[str, str] | None = None) -> str:
    entorno = os.environ if entorno is None else entorno
    dsn = entorno.get(VARIABLE_DSN)
    if not dsn:
        raise ConfiguracionInvalida(f"falta la variable {VARIABLE_DSN}")
    return validar_dsn_bot(dsn)


async def verificar_rol(pool) -> None:
    """Comprueba en la base que las conexiones del pool son de `bot_service` (current_user y session_user)."""
    fila = await pool.fetchrow("select current_user::text as actual, session_user::text as sesion")
    if fila is None or fila["actual"] != ROL_BOT or fila["sesion"] != ROL_BOT:
        raise ConfiguracionInvalida(f"el pool del bot no conecta como {ROL_BOT}")
