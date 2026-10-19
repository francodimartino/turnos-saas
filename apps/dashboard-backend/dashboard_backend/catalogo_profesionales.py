"""Dominio de los profesionales del local (ARCHITECTURE.md §7.1): sin FastAPI.

Cada función recibe la conexión de la transacción abierta por el endpoint y el tenant de la sesión. La auditoría se
escribe en la misma transacción que el cambio (si falla, el cambio se revierte) y lleva nombres de campos, nunca
valores ni datos personales.

El vínculo profesional-cuenta decide con qué cuenta se aprueban turnos como ese profesional, por eso cambiarlo es una
escalación de privilegios: se exige owner vivo (leído en la base, en la misma transacción del cambio) y el audit lleva
`vinculo: {anterior, nuevo}` con ids de `tenant_users`. Quien no es owner vivo provoca `ErrorSoloOwner`; que el
profesional no exista (o sea de otro tenant), `ErrorProfesionalInexistente`. Los errores de la base los traduce el
router.
"""

import uuid
from typing import Any

from platform_audit import registrar
from platform_auth import ErrorAuth, verificar_owner_vivo
from turnos_queries import listar_profesionales as _listar_profesionales

from dashboard_backend.sql_parcial import armar_set

COLUMNAS_PROFESIONAL = "id, tenant_id, name, is_active, tenant_user_id, created_at"

SQL_VER_PROFESIONAL = f"select {COLUMNAS_PROFESIONAL} from public.professionals where tenant_id = $1 and id = $2"
SQL_ALTA_PROFESIONAL = f"""
insert into public.professionals (tenant_id, name, tenant_user_id, is_active)
values ($1, $2, $3, $4)
returning {COLUMNAS_PROFESIONAL}
"""
SQL_VINCULO_PREVIO = "select tenant_user_id from public.professionals where tenant_id = $1 and id = $2 for update"
SQL_EXISTE_PROFESIONAL = "select 1 from public.professionals where tenant_id = $1 and id = $2"
SQL_BORRAR_PROFESIONAL = "delete from public.professionals where tenant_id = $1 and id = $2 returning id"

__all__ = [
    "ErrorProfesionalInexistente",
    "ErrorSoloOwner",
    "armar_update_profesional",
    "borrar_profesional",
    "crear_profesional",
    "existe_profesional",
    "listar_profesionales",
    "modificar_profesional",
    "ver_profesional",
]


class ErrorProfesionalInexistente(Exception):
    """El profesional no existe (o es de otro tenant)."""


class ErrorSoloOwner(Exception):
    """La operación exige owner vivo; `error` es el rechazo original de la capa de autenticación."""

    def __init__(self, error: ErrorAuth):
        super().__init__(str(error))
        self.error = error


def _bloque_vinculo(anterior: Any, nuevo: Any) -> dict[str, Any]:
    """Ids (nunca nombres) de la cuenta vinculada antes y después; vacío si no hay vínculo en el alta."""
    if anterior is None and nuevo is None:
        return {}
    return {
        "vinculo": {
            "anterior": None if anterior is None else str(anterior),
            "nuevo": None if nuevo is None else str(nuevo),
        }
    }


async def exigir_owner(conn: Any, sesion: Any) -> None:
    try:
        await verificar_owner_vivo(conn, sesion)
    except ErrorAuth as e:
        raise ErrorSoloOwner(e) from None


def armar_update_profesional(cambios: dict[str, Any]) -> tuple[str, list[Any]]:
    asignaciones, valores = armar_set(cambios, primer_parametro=3)
    sql = (
        f"update public.professionals set {asignaciones} where tenant_id = $1 and id = $2 "
        f"returning {COLUMNAS_PROFESIONAL}"
    )
    return sql, valores


async def listar_profesionales(
    conn: Any, tenant_id: uuid.UUID, incluir_inactivos: bool, limite: int, offset: int
) -> Any:
    return await _listar_profesionales(
        conn, tenant_id=tenant_id, incluir_inactivos=incluir_inactivos, limite=limite, offset=offset
    )


async def ver_profesional(conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_VER_PROFESIONAL, tenant_id, profesional_id)
    if fila is None:
        raise ErrorProfesionalInexistente
    return dict(fila)


async def existe_profesional(conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID) -> None:
    if await conn.fetchval(SQL_EXISTE_PROFESIONAL, tenant_id, profesional_id) is None:
        raise ErrorProfesionalInexistente


async def crear_profesional(
    conn: Any,
    sesion: Any,
    *,
    name: str,
    tenant_user_id: uuid.UUID | None,
    is_active: bool,
    campos: set[str],
) -> dict[str, Any]:
    """`campos` son los que el cliente envió; vincular una cuenta exige owner vivo (`ErrorSoloOwner`)."""
    if tenant_user_id is not None:
        await exigir_owner(conn, sesion)
    fila = await conn.fetchrow(SQL_ALTA_PROFESIONAL, sesion.tenant_id, name, tenant_user_id, is_active)
    await registrar(
        conn,
        "professional_created",
        "professionals",
        fila["id"],
        {"campos": sorted(campos | {"is_active"}), **_bloque_vinculo(None, tenant_user_id)},
    )
    return dict(fila)


async def modificar_profesional(
    conn: Any, sesion: Any, profesional_id: uuid.UUID, cambios: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = sesion.tenant_id
    sql, valores = armar_update_profesional(cambios)
    vinculo_previo = None
    cambia_vinculo = False
    if "tenant_user_id" in cambios:
        # Se lee con lock para decidir sobre el vínculo vigente y no sobre uno que otra transacción está cambiando.
        previo = await conn.fetchrow(SQL_VINCULO_PREVIO, tenant_id, profesional_id)
        if previo is not None:
            vinculo_previo = previo["tenant_user_id"]
            cambia_vinculo = vinculo_previo != cambios["tenant_user_id"]
            if cambia_vinculo:
                await exigir_owner(conn, sesion)
    fila = await conn.fetchrow(sql, tenant_id, profesional_id, *valores)
    if fila is None:
        raise ErrorProfesionalInexistente
    # Desactivar es un evento propio (en la base dispara el borrado del secret de Calendar).
    desactiva = cambios.get("is_active") is False
    await registrar(
        conn,
        "professional_deactivated" if desactiva else "professional_updated",
        "professionals",
        profesional_id,
        {
            "campos": sorted(cambios),
            **(_bloque_vinculo(vinculo_previo, fila["tenant_user_id"]) if cambia_vinculo else {}),
        },
    )
    return dict(fila)


async def borrar_profesional(conn: Any, tenant_id: uuid.UUID, profesional_id: uuid.UUID) -> None:
    fila = await conn.fetchrow(SQL_BORRAR_PROFESIONAL, tenant_id, profesional_id)
    if fila is None:
        raise ErrorProfesionalInexistente
    await registrar(conn, "professional_deleted", "professionals", profesional_id, None)
