"""platform_auth — ver ARCHITECTURE.md §2.4, §2.9, §3.1.5, §3.4, §3.6 y §4.3.

Verificación de JWT de Supabase Auth para `dashboard-backend` y `admin-backend`, con el estado del tenant y
del staff leído en vivo en cada request (§2.2, §2.9) y la regla de MFA (§3.6); chequeo de `permission_key` y de
escalación de privilegios (solo el owner vivo toca `is_owner` o los permisos de otro); invitaciones de staff por
la Auth Admin API de Supabase. Las apps no usan el SDK de Supabase: la identidad llega por el JWT que emite
Supabase Auth y se verifica acá.
"""

from platform_auth.dependencias import dependencia_admin, dependencia_dashboard, extraer_bearer
from platform_auth.errores import (
    ConfigJWTInvalida,
    ErrorAuth,
    MFARequerida,
    NoAutenticado,
    NoEsOwner,
    NoEsPlatformAdmin,
    PermisoDenegado,
    Prohibido,
    StaffInactivo,
    TenantNoActivo,
)
from platform_auth.identidad import IdentidadJWT
from platform_auth.invitaciones import (
    ClienteAuthAdmin,
    EmailInvalido,
    ErrorInvitacion,
    PermisoDesconocido,
    ResultadoInvitacion,
    StaffYaExiste,
    SupabaseAuthAdmin,
    invitar_staff,
)
from platform_auth.mfa import exigir_mfa
from platform_auth.permisos import (
    listar_permisos_vivos,
    requiere_owner_vivo,
    requiere_permiso,
    verificar_owner_vivo,
    verificar_permiso,
)
from platform_auth.sesion import SesionAdmin, SesionDashboard, autenticar_admin, autenticar_dashboard
from platform_auth.verificador import VerificadorJWT

__all__ = [
    "ClienteAuthAdmin",
    "ConfigJWTInvalida",
    "EmailInvalido",
    "ErrorAuth",
    "ErrorInvitacion",
    "IdentidadJWT",
    "MFARequerida",
    "NoAutenticado",
    "NoEsOwner",
    "NoEsPlatformAdmin",
    "PermisoDenegado",
    "PermisoDesconocido",
    "Prohibido",
    "ResultadoInvitacion",
    "SesionAdmin",
    "SesionDashboard",
    "StaffInactivo",
    "StaffYaExiste",
    "SupabaseAuthAdmin",
    "TenantNoActivo",
    "VerificadorJWT",
    "listar_permisos_vivos",
    "autenticar_admin",
    "autenticar_dashboard",
    "dependencia_admin",
    "dependencia_dashboard",
    "exigir_mfa",
    "extraer_bearer",
    "invitar_staff",
    "requiere_owner_vivo",
    "requiere_permiso",
    "verificar_owner_vivo",
    "verificar_permiso",
]
