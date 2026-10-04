"""Estado de conversación en `bot_sessions` (§2.6): paso actual y lo elegido hasta ahora.

Clave `(tenant_id, channel, channel_user_id)`. Una sesión con más de 1 hora sin actividad se descarta al
leerla. Se usa dentro de la transacción con contexto de tenant (la RLS filtra por tenant; el tenant también
va explícito en cada query).
"""

import json
import uuid
from typing import Any

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


async def leer_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> dict | None:
    """Estado vigente de la sesión, o `None` si no hay (o si venció, y entonces se borra)."""
    await conn.execute(_DESCARTAR_VENCIDA, tenant_id, canal, usuario)
    fila = await conn.fetchrow(_LEER, tenant_id, canal, usuario)
    if fila is None:
        return None
    estado = fila["state"]
    return json.loads(estado) if isinstance(estado, str) else dict(estado)


async def guardar_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, estado: dict) -> None:
    """Crea o actualiza la sesión y renueva su actividad."""
    await conn.execute(_GUARDAR, tenant_id, canal, usuario, json.dumps(estado))


async def borrar_sesion(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> None:
    await conn.execute(_BORRAR, tenant_id, canal, usuario)
