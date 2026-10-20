"""Estado de conversación en `bot_sessions` (§2.6): paso actual y lo elegido hasta ahora.

Clave `(tenant_id, channel, channel_user_id)`. Una sesión con más de 1 hora sin actividad se descarta al
leerla. Se usa dentro de la transacción con contexto de tenant (la RLS filtra por tenant; el tenant también
va explícito en cada query).
"""

import json
import logging
import uuid
from typing import Any

from turnos_bot.modelo_sesion import SesionBot, SesionInvalida

logger = logging.getLogger(__name__)

INACTIVIDAD = "1 hour"

_DESCARTAR_VENCIDA = (
    "delete from public.bot_sessions where tenant_id = $1 and channel = $2 and channel_user_id = $3 "
    f"and updated_at <= now() - interval '{INACTIVIDAD}'"
)
_LEER = "select state from public.bot_sessions where tenant_id = $1 and channel = $2 and channel_user_id = $3"
_GUARDAR = (
    "insert into public.bot_sessions (tenant_id, channel, channel_user_id, state, updated_at) "
    "values ($1, $2, $3, $4::jsonb, now()) "
    "on conflict (tenant_id, channel, channel_user_id) do update set state = excluded.state, updated_at = now()"
)
_BORRAR = "delete from public.bot_sessions where tenant_id = $1 and channel = $2 and channel_user_id = $3"


async def leer_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> SesionBot | None:
    """Sesión vigente, o `None` si no hay, si venció o si no es válida (versión distinta o forma inválida).

    En esos casos la fila se borra y el cliente vuelve al menú. El log no lleva datos del cliente.
    """
    await conn.execute(_DESCARTAR_VENCIDA, tenant_id, canal, usuario)
    fila = await conn.fetchrow(_LEER, tenant_id, canal, usuario)
    if fila is None:
        return None
    estado = fila["state"]
    try:
        return SesionBot.desde_json(json.loads(estado) if isinstance(estado, str) else estado)
    except (SesionInvalida, ValueError) as error:
        logger.warning("Sesión del bot descartada: %s", error)
        await borrar_sesion(conn, tenant_id, canal, usuario)
        return None


async def guardar_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, sesion: SesionBot) -> None:
    """Crea o actualiza la sesión y renueva su actividad."""
    await conn.execute(_GUARDAR, tenant_id, canal, usuario, json.dumps(sesion.a_json()))


async def borrar_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> None:
    await conn.execute(_BORRAR, tenant_id, canal, usuario)
