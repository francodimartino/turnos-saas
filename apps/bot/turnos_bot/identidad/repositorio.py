"""Acceso del bot a las fichas (`end_customers`) y a sus canales (`canales_cliente`), con el rol `bot_service`.

Todas las consultas filtran por `tenant_id` explícito además de la RLS (§1.7). Los datos personales
(`phone`, `name`, `extra_data`) solo viajan como parámetros y valores devueltos: nunca a logs ni al audit.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from platform_audit import registrar

_COLUMNAS = "ec.id, ec.tenant_id, ec.phone, ec.name, ec.extra_data, ec.deleted_at"

ACCION_CREAR_PERSONA = "create_end_customer"
ACCION_VINCULAR_CANAL = "link_customer_channel"
ACCION_VERIFICAR_CANAL = "verify_customer_channel"
ACCION_GUARDAR_DATO = "update_end_customer_data"


@dataclass(frozen=True)
class EndCustomer:
    """Ficha de `end_customers` ya identificada para el mensaje en curso."""

    id: uuid.UUID
    tenant_id: uuid.UUID
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
        phone=fila["phone"],
        name=fila["name"],
        extra_data=dict(extra or {}),
    )


async def _auditar(conn: Any, accion: str, end_customer_id: uuid.UUID, metadata: dict[str, str]) -> None:
    """Deja la constancia en `audit_log`: el actor es la propia persona (se fija en el contexto de la transacción).

    El metadata lleva solo el tipo de canal o el nombre del campo: los valores (id de Telegram, teléfono, nombre)
    son datos personales y no se copian.
    """
    await conn.execute("select set_config('app.current_actor_id', $1, true)", str(end_customer_id))
    await registrar(conn, accion, "end_customers", end_customer_id, metadata)


async def buscar_activo(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str) -> EndCustomer | None:
    """Ficha viva de la persona dueña de ese canal en este tenant (`deleted_at is null`), o `None`.

    Si el canal estaba declarado pero sin verificar (lo cargó el negocio), escribir desde él lo verifica: la
    plataforma del canal autentica al remitente.
    """
    fila = await conn.fetchrow(
        f"select {_COLUMNAS}, cc.id as canal_id, cc.verificado_at as canal_verificado_at "
        "from public.canales_cliente cc "
        "join public.end_customers ec on ec.tenant_id = cc.tenant_id and ec.id = cc.end_customer_id "
        "where cc.tenant_id = $1 and cc.canal = $2 and cc.identificador = $3 and ec.deleted_at is null",
        tenant_id,
        canal,
        usuario,
    )
    if fila is None:
        return None
    if fila["canal_verificado_at"] is None:
        await conn.execute(
            "update public.canales_cliente set verificado_at = now() "
            "where tenant_id = $1 and id = $2 and verificado_at is null",
            tenant_id,
            fila["canal_id"],
        )
        await _auditar(conn, ACCION_VERIFICAR_CANAL, fila["id"], {"canal": canal})
    return _a_end_customer(fila)


async def hay_perfil_dormido(conn: Any, canal: str, usuario: str) -> uuid.UUID | None:
    """Id de la ficha anonimizada más reciente de ese identificador (§2.3), o `None`.

    `find_dormant_end_customer` usa el contexto de tenant de la transacción y calcula el hash dentro de la base.
    """
    fila = await conn.fetchrow("select id from public.find_dormant_end_customer($1, $2)", canal, usuario)
    return None if fila is None else fila["id"]


async def crear(conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, telefono: str | None = None) -> EndCustomer:
    """Ficha nueva con su canal verificado: el remitente es quien escribe, por eso nace verificado (§2.12)."""
    fila = await conn.fetchrow(
        f"insert into public.end_customers as ec (tenant_id, phone) values ($1, $2) returning {_COLUMNAS}",
        tenant_id,
        telefono,
    )
    await conn.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, $3, $4, now())",
        tenant_id,
        fila["id"],
        canal,
        usuario,
    )
    await _auditar(conn, ACCION_CREAR_PERSONA, fila["id"], {"canal": canal})
    return _a_end_customer(fila)


async def vincular_alta_manual(
    conn: Any, tenant_id: uuid.UUID, canal: str, usuario: str, telefono: str
) -> EndCustomer | None:
    """Agrega el canal verificado a la ficha viva con ese teléfono (§2.8).

    Solo toma fichas todavía sin canal de chat (ni Telegram ni WhatsApp): una ficha ya ligada a otra cuenta no
    se pisa, porque eso le daría a quien conoce un número el perfil de otra persona. Un canal de email no lo
    impide: el email no se usa para unir en el bot. Con varias, la más antigua. `None` si no hay ninguna.
    """
    # Un lock por tenant y teléfono hasta el fin de la transacción: dos cuentas con el mismo número se serializan, y
    # la segunda ve (con su propia instantánea) el canal que la primera acaba de crear. Sin esto, el chequeo de
    # "sin canal de chat" corre sobre una instantánea anterior y ambas podrían quedar en la misma ficha.
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended($1::uuid::text || '|' || $2::text, 0))", tenant_id, telefono
    )
    fila = await conn.fetchrow(
        "with objetivo as ("
        "  select ec.id from public.end_customers ec "
        "   where ec.tenant_id = $1 and ec.phone = $4 and ec.deleted_at is null "
        "     and not exists (select 1 from public.canales_cliente cc "
        "                      where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id "
        "                        and cc.canal in ('telegram', 'whatsapp')) "
        "   order by ec.created_at, ec.id limit 1 for update skip locked), "
        "nuevo as ("
        "  insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "  select $1, objetivo.id, $2, $3, now() from objetivo returning end_customer_id) "
        f"select {_COLUMNAS} from public.end_customers ec join nuevo on ec.id = nuevo.end_customer_id "
        "where ec.tenant_id = $1",
        tenant_id,
        canal,
        usuario,
        telefono,
    )
    if fila is None:
        return None
    await _auditar(conn, ACCION_VINCULAR_CANAL, fila["id"], {"canal": canal})
    return _a_end_customer(fila)


async def reactivar(conn: Any, tenant_id: uuid.UUID, dormido_id: uuid.UUID, canal: str, usuario: str) -> EndCustomer:
    """Reactiva la ficha anonimizada (§2.3) con su canal verificado.

    `reactivate_end_customer` (base) comprueba que el pseudónimo de la ficha sea el de este identificador, la
    deja viva y crea el canal. `phone` y `name` siguen en null (los borró la anonimización): el flujo los pide.
    """
    # el actor del audit_log que deja la función es la propia persona
    await conn.execute("select set_config('app.current_actor_id', $1, true)", str(dormido_id))
    fila = await conn.fetchrow(
        "select id, tenant_id, phone, name, extra_data, deleted_at from public.reactivate_end_customer($1, $2, $3)",
        dormido_id,
        canal,
        usuario,
    )
    if fila is None or fila["tenant_id"] != tenant_id:
        raise LookupError("el perfil dormido ya no está disponible")
    return _a_end_customer(fila)


async def guardar_telefono(conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, telefono: str) -> None:
    await conn.execute(
        "update public.end_customers set phone = $3 where id = $2 and tenant_id = $1",
        tenant_id,
        end_customer_id,
        telefono,
    )
    await _auditar(conn, ACCION_GUARDAR_DATO, end_customer_id, {"campo": "phone"})


async def guardar_nombre(conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID, nombre: str) -> None:
    await conn.execute(
        "update public.end_customers set name = $3 where id = $2 and tenant_id = $1",
        tenant_id,
        end_customer_id,
        nombre,
    )
    await _auditar(conn, ACCION_GUARDAR_DATO, end_customer_id, {"campo": "name"})


async def anonimizar(conn: Any, end_customer_id: uuid.UUID, canal: str, usuario: str) -> None:
    """Borrado Ley 25.326, sin período de gracia (§2.3).

    El actor del `audit_log` es el propio end_customer: `anonymize_end_customer` lee `app.current_actor_id`.
    La función cancela los turnos `pending` futuros, borra las `bot_session` de todos los canales de la ficha y
    guarda el pseudónimo del canal desde el que se pidió el borrado (`canal`, `usuario`), para que el recontacto
    funcione desde esa misma cuenta.
    """
    await conn.execute("select set_config('app.current_actor_id', $1, true)", str(end_customer_id))
    await conn.execute("select set_config('app.requesting_channel', $1, true)", canal)
    await conn.execute("select set_config('app.requesting_channel_user_id', $1, true)", usuario)
    await conn.execute("select public.anonymize_end_customer($1)", end_customer_id)
