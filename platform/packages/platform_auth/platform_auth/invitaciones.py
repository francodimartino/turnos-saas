"""Alta de staff por invitación de Supabase Auth (§2.9).

El backend es el proxy de la Auth Admin API: el navegador nunca habla con Supabase. `invitar_staff` depende de la
interfaz `ClienteAuthAdmin` (§1.6), no del SDK ni de una URL; `SupabaseAuthAdmin` (`auth_admin.py`) es la
implementación sobre HTTP.

Orden y consistencia: primero se valida todo lo que puede fallar sin efectos (owner vivo, permisos, email), después se
llama a la API (que crea la cuenta de Auth y envía el mail) y recién entonces se escribe el `tenant_user`, sus permisos
y el audit en UNA transacción. Así:
  - si la API falla no queda ninguna fila (nunca un `tenant_user` huérfano) y el error dice si conviene reintentar;
  - si la API anduvo y la base falló, el reintento recibe la misma cuenta de Auth (la invitación a una cuenta aún no
    confirmada es idempotente) y `tenant_users.auth_user_id` es único: no se duplica nada;
  - reintentar una invitación ya completada devuelve `creado=False` y no toca los permisos existentes (cambiarlos es
    otra operación, de owner).
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from platform_audit import registrar
from platform_tenancy import tenant_tx

from platform_auth.errores import ErrorAuth
from platform_auth.permisos import verificar_owner_vivo
from platform_auth.sesion import SesionDashboard

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
_NOMBRE_MAX = 120


class EmailInvalido(ValueError):
    """El email de la invitación no tiene forma de email."""


class NombreInvalido(ValueError):
    """El nombre de la persona invitada falta o no es válido. El mensaje nunca repite el valor recibido."""


class PermisoDesconocido(ValueError):
    """Se pidió un `permission_key` que no existe en `permissions`. El mensaje nombra la clave."""


class StaffYaExiste(ErrorAuth):
    """409: esa cuenta no se puede dar de alta acá (§2.7: una persona, un solo tenant).

    El mensaje es neutro a propósito: no dice que la cuenta pertenece a otro negocio ni distingue entre una cuenta ya
    vinculada y una invitación pendiente, para que el dueño de un negocio no pueda averiguar quién es cliente de otro.
    """

    status_code = 409
    codigo = "staff_ya_existe"
    mensaje = "No se pudo agregar a esa persona con ese email"


class ClienteAuthAdmin(Protocol):
    """Lo que `platform_auth` necesita de la Auth Admin API. Implementable sin Supabase."""

    async def invitar_por_email(self, email: str, nombre: str | None = None) -> uuid.UUID:
        """Crea (o reutiliza, si aún no confirmó) la cuenta, envía la invitación y devuelve su id.

        `nombre` viaja como user metadata `full_name` (lo lee `public.get_tenant_staff_identity`).
        """
        ...


@dataclass(frozen=True)
class ResultadoInvitacion:
    tenant_user_id: uuid.UUID
    auth_user_id: uuid.UUID
    creado: bool  # False: ya existía en este tenant (reintento)


_SQL_PERMISOS = "select key from public.permissions"
_SQL_ALTA = """
insert into public.tenant_users (tenant_id, auth_user_id, is_owner, is_active)
values ($1, $2, false, true)
on conflict (auth_user_id) do nothing
returning id
"""
_SQL_EXISTENTE = "select id from public.tenant_users where tenant_id = $1 and auth_user_id = $2"
_SQL_ASIGNAR = """
insert into public.tenant_user_permissions (tenant_user_id, permission_key)
select $1::uuid, unnest($2::text[])
on conflict do nothing
"""


def _normalizar_email(email: Any) -> str:
    if not isinstance(email, str):
        raise EmailInvalido("el email debe ser un texto")
    limpio = email.strip().lower()
    if len(limpio) > 254 or not _EMAIL.match(limpio):
        raise EmailInvalido("el email no tiene formato válido")
    return limpio


def normalizar_nombre(nombre: Any) -> str:
    """Nombre completo de una persona: texto de 1 a 120 caracteres, espacios colapsados, sin caracteres de control.

    El mensaje de error nunca incluye el valor recibido (es un dato personal).
    """
    if not isinstance(nombre, str):
        raise NombreInvalido("el nombre es obligatorio")
    limpio = " ".join(nombre.split())
    if not limpio:
        raise NombreInvalido("el nombre es obligatorio")
    if len(limpio) > _NOMBRE_MAX:
        raise NombreInvalido(f"el nombre no puede superar {_NOMBRE_MAX} caracteres")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in limpio):
        raise NombreInvalido("el nombre tiene caracteres no permitidos")
    return limpio


async def invitar_staff(
    email: str,
    permisos: list[str] | tuple[str, ...],
    *,
    nombre: str,
    sesion: SesionDashboard,
    pool: Any,
    cliente: ClienteAuthAdmin,
) -> ResultadoInvitacion:
    """El owner (en vivo) invita a `email` como staff de su tenant con el set `permisos` (§2.9).

    `pool` es el de `dashboard_service`; `cliente` la Auth Admin API. Errores: `NoEsOwner`/`StaffInactivo` (403),
    `EmailInvalido`/`NombreInvalido`/`PermisoDesconocido` (datos), `ErrorInvitacion` (la API; mirar `reintentable`),
    `StaffYaExiste` (409).
    """
    email = _normalizar_email(email)
    nombre = normalizar_nombre(nombre)
    if isinstance(permisos, str):
        raise TypeError("permisos debe ser una lista de claves, no un texto")
    claves = sorted(set(permisos))

    # 1) Todo lo que puede rechazarse sin efectos, antes de llamar a la API.
    async with tenant_tx(pool, sesion.tenant_id, sesion.user_id) as conn:
        await verificar_owner_vivo(conn, sesion)
        existentes = {fila["key"] for fila in await conn.fetch(_SQL_PERMISOS)}
    desconocidos = [c for c in claves if c not in existentes]
    if desconocidos:
        raise PermisoDesconocido(f"permission_key inexistente: {desconocidos}")

    # 2) La API crea la cuenta de Auth y manda el mail. Si falla, no hay nada que deshacer.
    auth_user_id = await cliente.invitar_por_email(email, nombre)

    # 3) Una transacción: owner vivo de nuevo, alta, permisos y audit.
    async with tenant_tx(pool, sesion.tenant_id, sesion.user_id) as conn:
        await verificar_owner_vivo(conn, sesion)
        fila = await conn.fetchrow(_SQL_ALTA, sesion.tenant_id, auth_user_id)
        if fila is None:
            existente = await conn.fetchrow(_SQL_EXISTENTE, sesion.tenant_id, auth_user_id)
            if existente is None:
                raise StaffYaExiste()
            return ResultadoInvitacion(existente["id"], auth_user_id, creado=False)
        tenant_user_id = fila["id"]
        if claves:
            await conn.execute(_SQL_ASIGNAR, tenant_user_id, claves)
        await registrar(conn, "staff_invited", "tenant_user", tenant_user_id, {"permission_keys": claves})
    return ResultadoInvitacion(tenant_user_id, auth_user_id, creado=True)
