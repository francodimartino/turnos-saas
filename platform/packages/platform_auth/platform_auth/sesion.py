"""Autenticación de un request: JWT verificado + estado EN VIVO + regla de MFA.

Orden de chequeos (de barato a caro, sin tocar la base si ya se puede rechazar):
  1. JWT válido                                   -> 401
  2. dashboard: tenant_id presente / admin: claim  -> 401 / 403
  3. MFA según los claims                          -> 403
  4. lectura en vivo (tenant, staff / platform_admins) -> 403
  5. MFA otra vez con el `is_owner` en vivo        -> 403

Lo que se lee del JWT es identidad. `tenants.status` (§2.2), `tenant_users.is_active/is_owner` (§2.9) y la
pertenencia a `platform_admins` se consultan en la base en cada request: una suspensión o una baja corta las
sesiones abiertas ya, no cuando expira el token. Los errores de base se propagan (500), nunca se confunden con 401/403.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from platform_tenancy import admin_tx, tenant_tx

from platform_auth.errores import NoAutenticado, NoEsPlatformAdmin, StaffInactivo, TenantNoActivo
from platform_auth.identidad import IdentidadJWT
from platform_auth.mfa import exigir_mfa
from platform_auth.verificador import VerificadorJWT

# Una sola consulta: el tenant (RLS: solo su fila) y la fila de staff de ESTE usuario en ESE tenant.
_SQL_ESTADO_DASHBOARD = """
select t.status, u.is_owner, u.is_active
from public.tenants t
left join public.tenant_users u on u.tenant_id = t.id and u.auth_user_id = $2
where t.id = $1
"""
_SQL_ES_PLATFORM_ADMIN = "select true as existe from public.platform_admins where auth_user_id = $1"


@dataclass(frozen=True)
class SesionDashboard:
    """Sesión autorizada de staff de un tenant. `is_owner` es el valor en vivo, no el del claim."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    is_owner: bool
    aal: str | None
    identidad: IdentidadJWT


@dataclass(frozen=True)
class SesionAdmin:
    """Sesión autorizada de un platform admin."""

    user_id: uuid.UUID
    aal: str | None
    identidad: IdentidadJWT


def identificar_dashboard(token: str, *, verificador: VerificadorJWT, enrollment: bool = False) -> IdentidadJWT:
    """Pasos 1 a 3 del orden de chequeos, sin tocar la base: JWT válido, `tenant_id` presente y MFA por claims.

    Un token inválido, vencido o sin `tenant_id` se rechaza acá (401) antes de tomar una conexión del pool.
    """
    identidad = verificador.verificar(token)
    if identidad.tenant_id is None:
        # Franco, o staff desactivado (el Auth Hook emite tenant_id null): no es una sesión de dashboard.
        raise NoAutenticado()
    exigir_mfa(identidad, enrollment=enrollment)
    return identidad


async def sesion_dashboard_en_vivo(conn: Any, identidad: IdentidadJWT, *, enrollment: bool = False) -> SesionDashboard:
    """Pasos 4 y 5: lee el estado vivo (tenant, staff) con la conexión YA dentro de una transacción de tenant.

    `conn` trae `app.current_tenant_id` y `app.current_actor_id` de esta identidad (la abre quien llama, así el
    chequeo de sesión, el de permiso y el endpoint comparten una sola conexión y una sola transacción).
    """
    fila = await conn.fetchrow(_SQL_ESTADO_DASHBOARD, identidad.tenant_id, identidad.user_id)

    # No se distingue "no existe" de "no activo" hacia afuera: ambos son un negocio inaccesible.
    if fila is None or fila["status"] != "active":
        raise TenantNoActivo()
    if fila["is_active"] is not True:
        # Incluye "sin fila de staff" (is_active nulo por el left join) y staff desactivado.
        raise StaffInactivo()

    is_owner = fila["is_owner"] is True
    exigir_mfa(identidad, enrollment=enrollment, es_owner=is_owner)
    return SesionDashboard(
        user_id=identidad.user_id,
        tenant_id=identidad.tenant_id,
        is_owner=is_owner,
        aal=identidad.aal,
        identidad=identidad,
    )


async def autenticar_dashboard(
    token: str, *, verificador: VerificadorJWT, pool: Any, enrollment: bool = False
) -> SesionDashboard:
    """Autentica un request del `dashboard-backend` en su propia transacción. `pool` es el de `dashboard_service`.

    `enrollment=True` solo para el endpoint de enrollment de MFA (acepta `aal1` de owner/admin). Los endpoints con
    transacción propia del request usan `identificar_dashboard` + `sesion_dashboard_en_vivo` sobre esa transacción.
    """
    identidad = identificar_dashboard(token, verificador=verificador, enrollment=enrollment)
    async with tenant_tx(pool, identidad.tenant_id, identidad.user_id) as conn:
        return await sesion_dashboard_en_vivo(conn, identidad, enrollment=enrollment)


async def autenticar_admin(
    token: str, *, verificador: VerificadorJWT, pool: Any, enrollment: bool = False
) -> SesionAdmin:
    """Autentica un request del `admin-backend`. `pool` es el del rol `platform_admin`.

    El claim `is_platform_admin` es el gate; además se confirma en vivo contra `platform_admins`, así que
    quitar a un admin corta su sesión de inmediato.
    """
    identidad = verificador.verificar(token)
    if not identidad.is_platform_admin:
        raise NoEsPlatformAdmin()
    exigir_mfa(identidad, enrollment=enrollment)

    async with admin_tx(pool, identidad.user_id) as conn:
        fila = await conn.fetchrow(_SQL_ES_PLATFORM_ADMIN, identidad.user_id)
    if fila is None:
        raise NoEsPlatformAdmin()
    return SesionAdmin(user_id=identidad.user_id, aal=identidad.aal, identidad=identidad)
