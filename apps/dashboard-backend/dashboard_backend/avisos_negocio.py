"""Dominio de la configuración de avisos al negocio (ARCHITECTURE.md §2.12, §2.10): sin FastAPI.

Quién del staff recibe los avisos (reserva, cancelación, movimiento y pedido de aprobación) y por qué canal. Una fila de
`avisos_negocio_config` por miembro; quien no tiene fila recibe por defecto si es owner o tiene el permiso
`approve_bookings`, por email. Volver al default de una persona es borrar su fila. WhatsApp se muestra pero no se puede
activar: todavía no hay envío por ese canal y la base lo rechaza.

Cada función recibe la conexión de la transacción que abrió el endpoint (con el tenant y el actor ya fijados). La
auditoría se escribe en la misma transacción que el cambio: si falla, el cambio se revierte. La metadata lleva solo
ids y banderas, nunca nombres ni emails.
Un pedido que no cambia nada no escribe ni audita; si cambia algo, deja una sola fila de auditoría cuya metadata lista
solo a las personas que cambiaron.

Reglas que valen bajo escrituras simultáneas:
  - Las filas de `tenant_users` de las personas tocadas se bloquean (`for update`, en orden de id) antes de leer o
    escribir: dos cambios sobre la misma persona, o un cambio y su desactivación, se serializan. Una persona
    desactivada entre la validación y la escritura no se alcanza a configurar: el bloqueo la espera.
  - La garantía "el negocio nunca queda sin un destinatario de email" la da la base: un trigger de restricción toma un
    lock por negocio antes de contar, así que dos transacciones concurrentes no lo dejan en cero. Acá se difiere ese
    trigger mientras se aplican todos los cambios del pedido (intercambiar destinatarios en un solo pedido es
    válido, sin importar el orden) y se lo exige al final, dentro del `try` que traduce su rechazo a
    `ErrorSinDestinatarioDeAvisos`.
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from asyncpg.exceptions import PostgresError
from platform_audit import registrar
from platform_auth import SesionDashboard

from dashboard_backend.staff import SQL_IDENTIDAD

# Todos los activos del tenant, con lo que realmente rige para cada uno: la fila si existe y, si no, el default por rol.
SQL_LISTAR = """
select u.id, u.is_owner, (u.auth_user_id = $2) as es_yo, (c.tenant_user_id is null) as es_default,
  coalesce(c.recibe_avisos, d.por_defecto) as recibe_avisos,
  coalesce(c.canal_email, d.por_defecto) as canal_email,
  coalesce(c.canal_whatsapp, false) as canal_whatsapp
from public.tenant_users u
left join public.avisos_negocio_config c on c.tenant_id = u.tenant_id and c.tenant_user_id = u.id
cross join lateral (
  select u.is_owner or exists (
    select 1 from public.tenant_user_permissions p
    where p.tenant_user_id = u.id and p.permission_key = 'approve_bookings'
  ) as por_defecto
) d
where u.tenant_id = $1 and u.is_active
order by u.created_at, u.id
"""
# `for update` en orden de id: serializa con otro cambio a la misma persona y con su desactivación, sin deadlock.
SQL_BLOQUEAR_STAFF = """
select id, is_active from public.tenant_users
where tenant_id = $1 and id = any($2::uuid[])
order by id
for update
"""
SQL_ACTUALES = """
select tenant_user_id, recibe_avisos, canal_email, canal_whatsapp
from public.avisos_negocio_config
where tenant_id = $1 and tenant_user_id = any($2::uuid[])
"""
SQL_INSERTAR = """
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email, canal_whatsapp)
select $1::uuid, v.id, v.recibe_avisos, v.canal_email, v.canal_whatsapp
from unnest($2::uuid[], $3::boolean[], $4::boolean[], $5::boolean[])
  as v(id, recibe_avisos, canal_email, canal_whatsapp)
"""
SQL_ACTUALIZAR = """
update public.avisos_negocio_config c
set recibe_avisos = v.recibe_avisos, canal_email = v.canal_email, canal_whatsapp = v.canal_whatsapp
from unnest($2::uuid[], $3::boolean[], $4::boolean[], $5::boolean[])
  as v(id, recibe_avisos, canal_email, canal_whatsapp)
where c.tenant_id = $1 and c.tenant_user_id = v.id
"""
SQL_BORRAR = """
delete from public.avisos_negocio_config where tenant_id = $1 and tenant_user_id = any($2::uuid[])
"""
SQL_DIFERIR_GARANTIA = "set constraints avisos_negocio_exigir_destinatario deferred"
SQL_EXIGIR_GARANTIA = "set constraints avisos_negocio_exigir_destinatario immediate"

MAXIMO_CAMBIOS = 100
CANALES = (("email", True), ("whatsapp", False))
RESTRICCION_WHATSAPP = "avisos_negocio_config_whatsapp_deshabilitado"

__all__ = [
    "CANALES",
    "MAXIMO_CAMBIOS",
    "CambioAvisos",
    "ErrorCambioInvalido",
    "ErrorCanalNoDisponible",
    "ErrorSinDestinatarioDeAvisos",
    "ErrorStaffInactivo",
    "ErrorStaffInexistente",
    "ErrorStaffRepetido",
    "aplicar_cambios",
    "listar_avisos",
]


@dataclass(frozen=True)
class CambioAvisos:
    """Lo que se pide para una persona: volver al default, o fijar sus preferencias."""

    staff_id: uuid.UUID
    por_defecto: bool = False
    recibe_avisos: bool | None = None
    canal_email: bool | None = None
    canal_whatsapp: bool = False


class ErrorCambioInvalido(Exception):
    """El cambio mezcla `por_defecto` con preferencias, o no trae las dos preferencias."""


class ErrorCanalNoDisponible(Exception):
    """Se pidió activar WhatsApp, que todavía no envía."""


class ErrorStaffRepetido(Exception):
    """La misma persona aparece más de una vez en el pedido."""


class ErrorStaffInexistente(Exception):
    """Alguna persona no es del tenant (o no existe). No distingue los dos casos."""


class ErrorStaffInactivo(Exception):
    """Alguna persona está desactivada: no recibe avisos, así que no se la configura."""


class ErrorSinDestinatarioDeAvisos(Exception):
    """La base rechazó el cambio: el negocio quedaría sin ningún destinatario de avisos por email."""


@contextmanager
def _traducir_rechazos_de_la_base() -> Iterator[None]:
    try:
        yield
    except PostgresError as e:
        estado = getattr(e, "sqlstate", None)
        if estado == "23514":
            if "ningún destinatario de avisos" in str(e):
                raise ErrorSinDestinatarioDeAvisos from None
            if getattr(e, "constraint_name", None) == RESTRICCION_WHATSAPP:
                raise ErrorCanalNoDisponible from None
        if estado == "23503":  # clave foránea compuesta: la persona no es de este tenant
            raise ErrorStaffInexistente from None
        raise


def _validar(cambios: Sequence[CambioAvisos]) -> None:
    ids = [c.staff_id for c in cambios]
    if len(set(ids)) != len(ids):
        raise ErrorStaffRepetido
    for c in cambios:
        if c.canal_whatsapp:
            raise ErrorCanalNoDisponible
        if c.por_defecto:
            if c.recibe_avisos is not None or c.canal_email is not None:
                raise ErrorCambioInvalido
        elif c.recibe_avisos is None or c.canal_email is None:
            raise ErrorCambioInvalido


async def listar_avisos(conn: Any, sesion: SesionDashboard) -> dict[str, Any]:
    filas = await conn.fetch(SQL_LISTAR, sesion.tenant_id, sesion.user_id)
    identidades = {i["tenant_user_id"]: i for i in await conn.fetch(SQL_IDENTIDAD, sesion.tenant_id)}
    staff = []
    for f in filas:
        ident = identidades.get(f["id"])
        staff.append(
            {
                "id": str(f["id"]),
                "nombre": ident["nombre"] if ident is not None else None,
                "email": ident["email"] if ident is not None else None,
                "is_owner": f["is_owner"],
                "es_yo": f["es_yo"],
                "es_default": f["es_default"],
                "recibe_avisos": f["recibe_avisos"],
                "canal_email": f["canal_email"],
                "canal_whatsapp": f["canal_whatsapp"],
                "recibe_por_email": bool(f["recibe_avisos"] and f["canal_email"]),
            }
        )
    return {"staff": staff, "canales": [{"canal": c, "disponible": d} for c, d in CANALES]}


async def aplicar_cambios(conn: Any, sesion: SesionDashboard, cambios: Sequence[CambioAvisos]) -> dict[str, Any]:
    """Aplica los cambios en una sola transacción y devuelve la configuración resultante.

    Lo que no cambia nada (pedir el default de quien ya lo tiene, o las mismas preferencias que ya tiene) ni escribe
    ni audita.
    """
    _validar(cambios)
    ids = sorted({c.staff_id for c in cambios}, key=str)
    personas = {f["id"]: f for f in await conn.fetch(SQL_BLOQUEAR_STAFF, sesion.tenant_id, ids)}
    if len(personas) != len(ids):
        raise ErrorStaffInexistente
    if any(p["is_active"] is not True for p in personas.values()):
        raise ErrorStaffInactivo
    actuales = {f["tenant_user_id"]: f for f in await conn.fetch(SQL_ACTUALES, sesion.tenant_id, ids)}

    insertar: list[CambioAvisos] = []
    actualizar: list[CambioAvisos] = []
    borrar: list[uuid.UUID] = []
    for c in sorted(cambios, key=lambda x: str(x.staff_id)):
        actual = actuales.get(c.staff_id)
        if c.por_defecto:
            if actual is not None:
                borrar.append(c.staff_id)
        elif actual is None:
            insertar.append(c)
        elif (actual["recibe_avisos"], actual["canal_email"], actual["canal_whatsapp"]) != (
            c.recibe_avisos,
            c.canal_email,
            False,
        ):
            actualizar.append(c)

    if insertar or actualizar or borrar:
        with _traducir_rechazos_de_la_base():
            await conn.execute(SQL_DIFERIR_GARANTIA)
            for sql, lote in ((SQL_INSERTAR, insertar), (SQL_ACTUALIZAR, actualizar)):
                if lote:
                    await conn.execute(
                        sql,
                        sesion.tenant_id,
                        [c.staff_id for c in lote],
                        [bool(c.recibe_avisos) for c in lote],
                        [bool(c.canal_email) for c in lote],
                        [False for _ in lote],
                    )
            if borrar:
                await conn.execute(SQL_BORRAR, sesion.tenant_id, borrar)
            await conn.execute(SQL_EXIGIR_GARANTIA)
        modificados = [
            {
                "staff_id": str(c.staff_id),
                "por_defecto": False,
                "recibe_avisos": c.recibe_avisos,
                "canal_email": c.canal_email,
            }
            for c in [*insertar, *actualizar]
        ]
        vueltos_al_default = [
            {"staff_id": str(i), "por_defecto": True, "recibe_avisos": None, "canal_email": None} for i in borrar
        ]
        await registrar(
            conn,
            "avisos_negocio_updated",
            "avisos_negocio_config",
            None,
            {"cambios": modificados + vueltos_al_default},
        )
    return await listar_avisos(conn, sesion)
