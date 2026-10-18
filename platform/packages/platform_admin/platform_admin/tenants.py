"""create_tenant, reenvío de invitación, ciclo de vida y lectura de tenants (ARCHITECTURE.md §2.2, §2.11).

Todo corre con el rol `platform_admin` (pool de ese rol) dentro de `admin_tx`, sin contexto de tenant: las
policies explícitas `to platform_admin` y los grants son el límite real. Cada acción escribe `audit_log`
(el actor_type 'platform_admin' lo deriva la base del rol de la conexión) en la misma transacción que el cambio.
Las transiciones de `tenants.status` las hace cumplir un trigger de la base; acá se validan antes para devolver
un error de dominio claro, y si el trigger igual rechaza (carrera), también se traduce.

`create_tenant` no pasa por el chequeo de escalación de privilegios de platform_auth (§2.9): es otro code path.
"""

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from platform_audit import registrar
from platform_tenancy import admin_tx

from platform_admin.auth_admin import ClienteAuthAdmin
from platform_admin.errores import (
    DatosTenantInvalidos,
    InvitacionNoPendiente,
    OwnerNoDisponible,
    PurgaEnCurso,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
)

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NOMBRE_MAX = 120
_CAMPOS_ALTA = frozenset({"name", "slug", "settings"})
_ESTADOS = frozenset({"active", "suspended", "inactive", "purging", "purged"})

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"

# Hay algún tenant con una invitación pendiente para este email? (case-insensitive; el alta falla antes de crear nada)
_SQL_INVITACION_PENDIENTE_AJENA = (
    "select exists (select 1 from public.tenant_users where auth_user_id is null "
    "and pending_invite_email is not null and lower(pending_invite_email) = lower($1))"
)

INVITE_OK = "ok"
INVITE_PENDIENTE = "invite_pendiente"

# acción -> (auditoría, estados de origen válidos, estado destino)
_TRANSICIONES = {
    "suspender": ("tenant_suspend", frozenset({"active"}), "suspended"),
    "levantar_suspension": ("tenant_unsuspend", frozenset({"suspended"}), "active"),
    "dar_de_baja": ("tenant_deactivate", frozenset({"active", "suspended"}), "inactive"),
    "reactivar": ("tenant_reactivate", frozenset({"inactive"}), "active"),
}


@dataclass(frozen=True)
class ResultadoAlta:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    estado: str  # INVITE_OK | INVITE_PENDIENTE


@dataclass(frozen=True)
class ResultadoInvitacion:
    tenant_id: uuid.UUID
    estado: str  # INVITE_OK | INVITE_PENDIENTE


@dataclass(frozen=True)
class TenantAdmin:
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    status: str
    deactivated_at: datetime | None
    paid_until: date | None
    created_at: datetime
    invitacion_pendiente: bool
    owner_email: str | None  # de `get_tenant_staff_identity`: cuenta vinculada o email de la invitación pendiente
    owner_nombre: str | None  # `full_name` de la cuenta; null mientras no vincule la cuenta


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)


def _validar_nombre_persona(nombre: Any) -> str:
    """Nombre completo de una persona: 1 a 120 caracteres, espacios colapsados, sin caracteres de control.

    El mensaje nunca incluye el valor recibido (dato personal).
    """
    if not isinstance(nombre, str):
        raise DatosTenantInvalidos("owner_nombre es obligatorio")
    limpio = " ".join(nombre.split())
    if not limpio:
        raise DatosTenantInvalidos("owner_nombre es obligatorio")
    if len(limpio) > _NOMBRE_MAX:
        raise DatosTenantInvalidos(f"owner_nombre no puede superar {_NOMBRE_MAX} caracteres")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in limpio):
        raise DatosTenantInvalidos("owner_nombre tiene caracteres no permitidos")
    return limpio


def _validar_alta(tenant_data: Any, owner_email: Any) -> tuple[str, str, dict]:
    if not isinstance(tenant_data, dict):
        raise DatosTenantInvalidos("tenant_data debe ser un objeto")
    desconocidos = set(tenant_data) - _CAMPOS_ALTA
    if desconocidos:
        # status/plan/deactivated_at... no se aceptan del input: el alta siempre nace 'active' y 'pilot'.
        raise DatosTenantInvalidos(f"Campos no permitidos en el alta: {sorted(map(str, desconocidos))}")
    nombre = tenant_data.get("name")
    if not isinstance(nombre, str) or not nombre.strip():
        raise DatosTenantInvalidos("name es obligatorio")
    slug = tenant_data.get("slug")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise DatosTenantInvalidos("slug es obligatorio: minúsculas, números y guiones")
    settings = tenant_data.get("settings", {})
    if not isinstance(settings, dict):
        raise DatosTenantInvalidos("settings debe ser un objeto")
    if not isinstance(owner_email, str) or not _EMAIL.match(owner_email):
        raise DatosTenantInvalidos("owner_email no es un email válido")
    return nombre.strip(), slug, settings


async def _guardar_invite_fallido(
    conn: Any, tenant_id: uuid.UUID, owner_id: uuid.UUID, owner_email: str, owner_nombre: str | None, exc: Exception
) -> None:
    """El invite falló: email y nombre quedan guardados para reintentar (los anula un trigger al vincular la cuenta).

    Nunca van al audit.
    """
    await conn.execute(
        "update public.tenant_users set pending_invite_email = $2, pending_invite_name = $3 "
        "where id = $1 and auth_user_id is null",
        owner_id,
        owner_email,
        owner_nombre,
    )
    await registrar(
        conn, "tenant_invite_failed", "tenant_users", owner_id, {"motivo": type(exc).__name__}, tenant_id=tenant_id
    )


async def _vincular_owner(conn: Any, tenant_id: uuid.UUID, owner_id: uuid.UUID, auth_user_id: uuid.UUID) -> None:
    await conn.execute(
        "update public.tenant_users set auth_user_id = $2 where id = $1 and auth_user_id is null",
        owner_id,
        auth_user_id,
    )
    await registrar(conn, "tenant_invite_sent", "tenant_users", owner_id, tenant_id=tenant_id)


async def _invitar_y_registrar(
    pool: Any,
    cliente: ClienteAuthAdmin,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    owner_email: str,
    owner_nombre: str | None,
    actor_id: Any,
) -> str:
    """Reenvío de la invitación del owner. Un fallo del invite no revierte nada: queda pendiente.

    El nombre viaja a la Auth Admin API (user metadata). Si el invite falla se guarda en
    `pending_invite_name` junto al email (lo anula un trigger al vincular la cuenta); nunca va al audit.
    """
    try:
        auth_user_id = await cliente.invitar(owner_email, owner_nombre)
    except Exception as exc:  # cualquier fallo del cliente (red, 4xx/5xx) deja la invitación pendiente
        async with admin_tx(pool, actor_id) as conn:
            await _guardar_invite_fallido(conn, tenant_id, owner_id, owner_email, owner_nombre, exc)
        return INVITE_PENDIENTE
    async with admin_tx(pool, actor_id) as conn:
        await _vincular_owner(conn, tenant_id, owner_id, auth_user_id)
    return INVITE_OK


async def _invitar_en_alta(
    conn: Any,
    cliente: ClienteAuthAdmin,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    owner_email: str,
    owner_nombre: str,
) -> str:
    """Paso 2 del alta, dentro de la misma transacción que el paso 1.

    Si la Auth Admin API devuelve una cuenta que ya está vinculada a otro tenant (la invitación a una cuenta aún no
    confirmada es idempotente), `auth_user_id` es único: la violación se traduce a `OwnerNoDisponible` y la
    transacción entera se revierte (no queda ni el tenant ni el owner). Un fallo del invite en sí no revierte
    nada: queda pendiente.
    """
    try:
        auth_user_id = await cliente.invitar(owner_email, owner_nombre)
    except Exception as exc:  # cualquier fallo del cliente (red, 4xx/5xx) deja la invitación pendiente
        await _guardar_invite_fallido(conn, tenant_id, owner_id, owner_email, owner_nombre, exc)
        return INVITE_PENDIENTE
    try:
        await _vincular_owner(conn, tenant_id, owner_id, auth_user_id)
    except Exception as exc:
        if _sqlstate(exc) == _UNIQUE_VIOLATION:
            raise OwnerNoDisponible() from None
        raise
    return INVITE_OK


async def create_tenant(
    pool: Any,
    cliente_auth: ClienteAuthAdmin,
    tenant_data: dict,
    owner_email: str,
    *,
    owner_nombre: str,
    actor_id: Any,
) -> ResultadoAlta:
    """Alta de un tenant en dos pasos dentro de UNA transacción (§2.2).

    1) Se crea el tenant y su primer `tenant_user` (`is_owner=true`, sin cuenta todavía).
    2) Se invita a `owner_email` por la Auth Admin API y se vincula la cuenta al owner.
    Si el email ya tiene cuenta vinculada o invitación pendiente en otro tenant, `OwnerNoDisponible` (409) y se
    revierte todo: no queda ni el tenant ni el owner (el rol no puede borrarlos después, por eso se decide acá).
    Si el invite falla (red, 4xx/5xx), el tenant y el owner quedan creados y el estado devuelto es `invite_pendiente`
    (se reintenta con `reenviar_invitacion`). Si el invite falla, el email y el nombre se guardan en
    `tenant_users.pending_invite_email` / `pending_invite_name` (los anula la base al vincular la cuenta); nunca
    van al audit ni a logs (PII).

    `owner_nombre` (obligatorio) es el nombre completo del owner: viaja a la Auth Admin API como user metadata
    `full_name` y se lee después con `public.get_tenant_staff_identity` (que devuelve el pendiente mientras la cuenta
    no existe). Nunca va al audit ni a logs.
    """
    nombre, slug, settings = _validar_alta(tenant_data, owner_email)
    owner_nombre = _validar_nombre_persona(owner_nombre)
    async with admin_tx(pool, actor_id) as conn:
        if await conn.fetchval(_SQL_INVITACION_PENDIENTE_AJENA, owner_email):
            raise OwnerNoDisponible()
        try:
            tenant_id = await conn.fetchval(
                "insert into public.tenants (name, slug, settings) values ($1, $2, $3::jsonb) returning id",
                nombre,
                slug,
                json.dumps(settings),
            )
        except Exception as exc:
            if _sqlstate(exc) == _UNIQUE_VIOLATION:
                raise SlugEnUso("Ya existe un tenant con ese slug") from None
            raise
        owner_id = await conn.fetchval(
            "insert into public.tenant_users (tenant_id, is_owner) values ($1, true) returning id", tenant_id
        )
        await registrar(conn, "tenant_create", "tenants", tenant_id, {"slug": slug}, tenant_id=tenant_id)
        estado = await _invitar_en_alta(conn, cliente_auth, tenant_id, owner_id, owner_email, owner_nombre)
    return ResultadoAlta(tenant_id=tenant_id, owner_id=owner_id, estado=estado)


async def reenviar_invitacion(
    pool: Any,
    cliente_auth: ClienteAuthAdmin,
    tenant_id: Any,
    owner_email: str | None = None,
    *,
    owner_nombre: str | None = None,
    actor_id: Any,
) -> ResultadoInvitacion:
    """Reintenta la invitación del owner de un tenant cuyo invite falló.

    Usa el email guardado en `pending_invite_email`; si se pasa `owner_email` explícito (para corregirlo),
    reemplaza al guardado. Sin email guardado ni explícito, o con el owner ya vinculado, es `InvitacionNoPendiente`.

    `owner_nombre` es opcional: sin él se usa el guardado en `pending_invite_name`; si se pasa, lo reemplaza.
    """
    if owner_nombre is not None:
        owner_nombre = _validar_nombre_persona(owner_nombre)
    if owner_email is not None and (not isinstance(owner_email, str) or not _EMAIL.match(owner_email)):
        raise DatosTenantInvalidos("owner_email no es un email válido")
    async with admin_tx(pool, actor_id) as conn:
        owner = await conn.fetchrow(
            "select id, auth_user_id, pending_invite_email, pending_invite_name from public.tenant_users "
            "where tenant_id = $1 and is_owner order by created_at, id limit 1",
            tenant_id,
        )
    if owner is None:
        raise TenantNoEncontrado("El tenant no existe o no tiene owner")
    if owner["auth_user_id"] is not None:
        raise InvitacionNoPendiente("El owner ya tiene cuenta: no hay invitación pendiente")
    email = owner_email or owner["pending_invite_email"]
    if not email:
        raise InvitacionNoPendiente("No hay un email de invitación guardado: pasá el email del owner")
    nombre = owner_nombre or owner["pending_invite_name"]
    estado = await _invitar_y_registrar(pool, cliente_auth, tenant_id, owner["id"], email, nombre, actor_id)
    return ResultadoInvitacion(tenant_id=tenant_id, estado=estado)


async def _cambiar_estado(pool: Any, tenant_id: Any, accion: str, actor_id: Any) -> None:
    auditoria, origenes, destino = _TRANSICIONES[accion]
    desde: str | None = None
    try:
        async with admin_tx(pool, actor_id) as conn:
            # for update: serializa dos acciones concurrentes sobre el mismo tenant
            desde = await conn.fetchval("select status from public.tenants where id = $1 for update", tenant_id)
            if desde is None:
                raise TenantNoEncontrado("El tenant no existe")
            if desde == "purging":
                raise PurgaEnCurso(accion)
            if desde not in origenes:
                raise TransicionInvalida(accion, desde)
            # deactivated_at lo maneja el trigger: arranca el reloj al pasar a inactive y lo limpia al volver
            await conn.execute("update public.tenants set status = $2 where id = $1", tenant_id, destino)
            await registrar(
                conn, auditoria, "tenants", tenant_id, {"desde": desde, "hasta": destino}, tenant_id=tenant_id
            )
    except (TenantNoEncontrado, TransicionInvalida):
        raise
    except Exception as exc:
        if _sqlstate(exc) == _CHECK_VIOLATION:
            raise TransicionInvalida(accion, desde) from None
        raise


async def suspender(pool: Any, tenant_id: Any, *, actor_id: Any) -> None:
    """active -> suspended (contención de un incidente: no arranca el reloj de purga)."""
    await _cambiar_estado(pool, tenant_id, "suspender", actor_id)


async def levantar_suspension(pool: Any, tenant_id: Any, *, actor_id: Any) -> None:
    """suspended -> active."""
    await _cambiar_estado(pool, tenant_id, "levantar_suspension", actor_id)


async def dar_de_baja(pool: Any, tenant_id: Any, *, actor_id: Any) -> None:
    """active/suspended -> inactive (arranca el período de gracia: `deactivated_at = now()`)."""
    await _cambiar_estado(pool, tenant_id, "dar_de_baja", actor_id)


async def reactivar(pool: Any, tenant_id: Any, *, actor_id: Any) -> None:
    """inactive -> active, dentro del período de gracia. Un `purging` (`PurgaEnCurso`) o `purged` se rechaza."""
    await _cambiar_estado(pool, tenant_id, "reactivar", actor_id)


_SELECT_TENANTS = """
select t.id, t.name, t.slug, t.plan, t.status, t.deactivated_at, t.paid_until, t.created_at,
       exists (
         select 1 from public.tenant_users u
          where u.tenant_id = t.id and u.is_owner and u.auth_user_id is null
       ) as invitacion_pendiente,
       o.email as owner_email,
       o.nombre as owner_nombre
  from public.tenants t
  left join lateral (
    -- identidad del primer owner del tenant (nombre y email viven en auth.users: se leen con la función definer)
    select i.email, i.nombre
      from public.tenant_users u
      join public.get_tenant_staff_identity(t.id) i on i.tenant_user_id = u.id
     where u.tenant_id = t.id and u.is_owner
     order by u.created_at, u.id
     limit 1
  ) o on true
"""


def _tenant(fila: Any) -> TenantAdmin:
    return TenantAdmin(**{campo: fila[campo] for campo in TenantAdmin.__dataclass_fields__})


async def listar_tenants(pool: Any, *, actor_id: Any = None, status: str | None = None) -> list[TenantAdmin]:
    """Todos los tenants, sin contexto de tenant (policy explícita de `platform_admin`)."""
    if status is not None and status not in _ESTADOS:
        raise DatosTenantInvalidos(f"status inválido: {status!r}")
    async with admin_tx(pool, actor_id) as conn:
        if status is None:
            filas = await conn.fetch(_SELECT_TENANTS + " order by t.created_at desc, t.id")
        else:
            filas = await conn.fetch(_SELECT_TENANTS + " where t.status = $1 order by t.created_at desc, t.id", status)
    return [_tenant(f) for f in filas]


async def obtener_tenant(pool: Any, tenant_id: Any, *, actor_id: Any = None) -> TenantAdmin:
    async with admin_tx(pool, actor_id) as conn:
        fila = await conn.fetchrow(_SELECT_TENANTS + " where t.id = $1", tenant_id)
    if fila is None:
        raise TenantNoEncontrado("El tenant no existe")
    return _tenant(fila)
