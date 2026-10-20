"""platform_auth — ver ARCHITECTURE.md §2.4, §2.9, §3.1.5, §3.4, §3.6 y §4.3.

Verificación de JWT de Supabase Auth para `dashboard-backend` y `admin-backend`, con el estado del tenant y
del staff leído en vivo en cada request (§2.2, §2.9) y la regla de MFA (§3.6); chequeo de `permission_key` y de
escalación de privilegios (solo el owner vivo toca `is_owner` o los permisos de otro); invitaciones de staff por
la Auth Admin API de Supabase. Las apps no usan el SDK de Supabase: la identidad llega por el JWT que emite
Supabase Auth y se verifica acá.
"""

from platform_auth.auth_admin import ErrorEliminacionCuenta, ErrorInvitacion, SupabaseAuthAdmin
from platform_auth.catalogo_permisos import Permiso, claves_de_permisos
from platform_auth.dependencias import (
    dependencia_admin,
    dependencia_dashboard,
    dependencia_identidad_dashboard,
    extraer_bearer,
)
from platform_auth.errores import (
    ClavesNoDisponibles,
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
    NombreInvalido,
    PermisoDesconocido,
    ResultadoInvitacion,
    StaffYaExiste,
    invitar_staff,
    normalizar_nombre,
)
from platform_auth.mfa import exigir_mfa
from platform_auth.permisos import (
    listar_permisos_vivos,
    requiere_owner_vivo,
    requiere_permiso,
    verificar_owner_vivo,
    verificar_permiso,
)
from platform_auth.sesion import (
    SesionAdmin,
    SesionDashboard,
    autenticar_admin,
    autenticar_dashboard,
    identificar_dashboard,
    sesion_dashboard_en_vivo,
)
from platform_auth.verificador import VerificadorJWT

__all__ = [
    "ClavesNoDisponibles",
    "ClienteAuthAdmin",
    "ConfigJWTInvalida",
    "EmailInvalido",
    "NombreInvalido",
    "normalizar_nombre",
    "ErrorAuth",
    "ErrorEliminacionCuenta",
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
    "Permiso",
    "claves_de_permisos",
    "listar_permisos_vivos",
    "autenticar_admin",
    "autenticar_dashboard",
    "dependencia_admin",
    "dependencia_dashboard",
    "dependencia_identidad_dashboard",
    "identificar_dashboard",
    "sesion_dashboard_en_vivo",
    "exigir_mfa",
    "extraer_bearer",
    "invitar_staff",
    "requiere_owner_vivo",
    "requiere_permiso",
    "verificar_owner_vivo",
    "verificar_permiso",
]
