"""Acceso a `end_customers` del bot (rol `bot_service`: select/insert/update, sin delete).

Todas las consultas filtran por `tenant_id` explícito además de la RLS (§1.7). Los datos personales
(`phone`, `name`, `extra_data`) solo viajan como parámetros y valores devueltos: nunca a logs ni al audit.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

_COLUMNAS = "id, tenant_id, channel, channel_user_id, phone, name, extra_data, deleted_at"


@dataclass(frozen=True)
class EndCustomer:
    """Fila de `end_customers` ya identificada para el mensaje en curso."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    channel: str | None
    channel_user_id: str | None
    phone: str | None = field(repr=False)
    name: str | None = field(repr=False)
    extra_data: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def completo(self) -> bool:
        """Tiene los datos mínimos para operar (teléfono y nombre); si no, hay que terminar el alta."""
        return bool(self.phone) and bool(self.name)


def _a_end_customer(fila: Any) -> EndCustomer:
    extra = fila["extra_data"]
    if isinstance(extra, str):
        extra = json.loads(extra)
    return EndCustomer(
        id=fila["id"],
        tenant_id=fila["tenant_id"],
        channel=fila["channel"],
        channel_user_id=fila["channel_user_id"],
        phone=fila["phone"],
        name=fila["name"],
        extra_data=dict(extra or {}),
    )


async def buscar_activo(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> EndCustomer | None:
    """Fila viva del identificador en este tenant (`deleted_at is null`), o `None`."""
    fila = await conn.fetchrow(
        f"select {_COLUMNAS} from public.end_customers "
        "where tenant_id = $1 and channel = $2 and channel_user_id = $3 and deleted_at is null",
        tenant_id,
        canal,
        usuario,
    )
    return None if fila is None else _a_end_customer(fila)


async def hay_perfil_dormido(conn: Any, canal: str, usuario: str) -> uuid.UUID | None:
    """Id de la fila anonimizada más reciente de ese identificador (§2.3), o `None`.

    `find_dormant_end_customer` usa el contexto de tenant de la transacción y calcula el hash dentro de la base.
    """
    fila = await conn.fetchrow("select id from public.find_dormant_end_customer($1, $2)", canal, usuario)
    return None if fila is None else fila["id"]


async def crear(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, telefono: str | None = None) -> EndCustomer:
    fila = await conn.fetchrow(
        "insert into public.end_customers (tenant_id, channel, channel_user_id, phone) "
        f"values ($1, $2, $3, $4) returning {_COLUMNAS}",
        tenant_id,
        canal,
        usuario,
        telefono,
    )
    return _a_end_customer(fila)


async def vincular_alta_manual(
    conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, telefono: str
) -> EndCustomer | None:
    """Vincula el canal a la fila de alta manual con ese teléfono (§2.8).

    Solo toma filas vivas y todavía sin canal (`channel is null`): una fila ya ligada a otra cuenta no se
    pisa, porque eso le daría a quien conoce un número el perfil de otra persona. Con varias, la más antigua.
    `None` si no hay ninguna.
    """
    fila = await conn.fetchrow(
        "update public.end_customers set channel = $2, channel_user_id = $3 "
        "where id = (select id from public.end_customers "
        "            where tenant_id = $1 and phone = $4 and channel is null and channel_user_id is null "
        "              and channel_user_hash is null and deleted_at is null "
        "            order by created_at, id limit 1 for update skip locked) "
        f"and tenant_id = $1 returning {_COLUMNAS}",
        tenant_id,
        canal,
        usuario,
        telefono,
    )
    return None if fila is None else _a_end_customer(fila)


async def reactivar(conn: Any, tenant_id: uuid.UUID, dormido_id: uuid.UUID, canal: str, usuario: str) -> EndCustomer:
    """Reactiva el perfil anonimizado (§2.3): restaura `channel_user_id`, borra el hash y `deleted_at`.

    `phone` y `name` siguen en null (los borró la anonimización): el flujo los vuelve a pedir.
    """
    fila = await conn.fetchrow(
        "update public.end_customers set channel_user_id = $4, channel_user_hash = null, deleted_at = null "
        "where id = $2 and tenant_id = $1 and channel = $3 and channel_user_id is null "
        f"and deleted_at is not null returning {_COLUMNAS}",
        tenant_id,
        dormido_id,
        canal,
        usuario,
    )
    if fila is None:
        raise LookupError("el perfil dormido ya no está disponible")
    return _a_end_customer(fila)


async def guardar_telefono(conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, telefono: str) -> None:
    await conn.execute(
        "update public.end_customers set phone = $3 where id = $2 and tenant_id = $1",
        tenant_id,
        end_customer_id,
        telefono,
    )


async def guardar_nombre(conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, nombre: str) -> None:
    await conn.execute(
        "update public.end_customers set name = $3 where id = $2 and tenant_id = $1",
        tenant_id,
        end_customer_id,
        nombre,
    )


async def anonimizar(conn: Any, end_customer_id: uuid.UUID) -> None:
    """Borrado Ley 25.326, sin período de gracia (§2.3).

    El actor del `audit_log` es el propio end_customer: `anonymize_end_customer` lee `app.current_actor_id`.
    La función cancela los turnos `pending` futuros y borra la `bot_session` del usuario.
    """
    await conn.execute("select set_config('app.current_actor_id', $1, true)", str(end_customer_id))
    await conn.execute("select public.anonymize_end_customer($1)", end_customer_id)
