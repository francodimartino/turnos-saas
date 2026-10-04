"""platform_auth — ver ARCHITECTURE.md §3.4, §3.6 y §4.3.

Verificación de JWT de Supabase Auth para `dashboard-backend` y `admin-backend`, con el estado del tenant y
del staff leído en vivo en cada request (§2.2, §2.9) y la regla de MFA (§3.6). Las apps no usan el SDK de
Supabase: la identidad llega por el JWT que emite Supabase Auth y se verifica acá.
"""

from platform_auth.dependencias import dependencia_admin, dependencia_dashboard, extraer_bearer
from platform_auth.errores import (
    ConfigJWTInvalida,
    ErrorAuth,
    MFARequerida,
    NoAutenticado,
    NoEsPlatformAdmin,
    Prohibido,
    StaffInactivo,
    TenantNoActivo,
)
from platform_auth.identidad import IdentidadJWT
from platform_auth.mfa import exigir_mfa
from platform_auth.sesion import SesionAdmin, SesionDashboard, autenticar_admin, autenticar_dashboard
from platform_auth.verificador import VerificadorJWT

__all__ = [
    "ConfigJWTInvalida",
    "ErrorAuth",
    "IdentidadJWT",
    "MFARequerida",
    "NoAutenticado",
    "NoEsPlatformAdmin",
    "Prohibido",
    "SesionAdmin",
    "SesionDashboard",
    "StaffInactivo",
    "TenantNoActivo",
    "VerificadorJWT",
    "autenticar_admin",
    "autenticar_dashboard",
    "dependencia_admin",
    "dependencia_dashboard",
    "exigir_mfa",
    "extraer_bearer",
]
