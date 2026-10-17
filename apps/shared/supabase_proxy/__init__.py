"""Cliente del proxy de Supabase Auth (GoTrue) compartido por dashboard-backend y admin-backend.

El navegador nunca habla con Supabase (ARCHITECTURE.md §3.1.5): cada backend usa este cliente, con la anon key.
"""

from supabase_proxy.cliente import (
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
