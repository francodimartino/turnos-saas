"""Reexporta el cliente de Supabase Auth, que vive en `apps/shared/supabase_proxy` (lo usa también admin-backend)."""

from supabase_proxy import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CodigoMFAInvalido,
    ContrasenaDebil,
    CredencialesInvalidas,
    EnrolamientoTOTP,
    ErrorSupabaseAuth,
    InvitacionInvalida,
    LimiteExcedido,
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
    "SesionExpirada",
    "SolicitudRechazada",
    "TokensSesion",
]
