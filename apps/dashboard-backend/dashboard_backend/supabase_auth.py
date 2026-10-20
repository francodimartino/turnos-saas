"""Reexporta el cliente de Supabase Auth (`apps/shared/turnos_supabase_proxy`, lo usa también admin-backend)."""

from turnos_supabase_proxy import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CodigoMFAInvalido,
    ContrasenaDebil,
    CredencialesInvalidas,
    EnrolamientoTOTP,
    ErrorSupabaseAuth,
    InvitacionInvalida,
    LimiteExcedido,
    MFAYaConfigurada,
    SesionExpirada,
    SolicitudRechazada,
    TokensSesion,
)

__all__ = [
    "AuthNoDisponible",
    "ClienteSupabaseAuth",
    "CodigoMFAInvalido",
    "ContrasenaDebil",
    "CredencialesInvalidas",
    "EnrolamientoTOTP",
    "ErrorSupabaseAuth",
    "InvitacionInvalida",
    "LimiteExcedido",
    "MFAYaConfigurada",
    "SesionExpirada",
    "SolicitudRechazada",
    "TokensSesion",
]
