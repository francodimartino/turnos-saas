"""platform_admin — operaciones del panel admin sobre tenants (ARCHITECTURE.md §2.2, §2.11, §4.3)."""

from platform_admin.auth_admin import ClienteAuthAdmin, ClienteAuthAdminHttp
from platform_admin.errores import (
    DatosTenantInvalidos,
    ErrorDominioAdmin,
    ErrorInvitacion,
    InvitacionNoPendiente,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
)
from platform_admin.tenants import (
    INVITE_OK,
    INVITE_PENDIENTE,
    ResultadoAlta,
    ResultadoInvitacion,
    TenantAdmin,
    create_tenant,
    dar_de_baja,
    levantar_suspension,
    listar_tenants,
    obtener_tenant,
    reactivar,
    reenviar_invitacion,
    suspender,
)

__all__ = [
    "INVITE_OK",
    "INVITE_PENDIENTE",
    "ClienteAuthAdmin",
    "ClienteAuthAdminHttp",
    "DatosTenantInvalidos",
    "ErrorDominioAdmin",
    "ErrorInvitacion",
    "InvitacionNoPendiente",
    "ResultadoAlta",
    "ResultadoInvitacion",
    "SlugEnUso",
    "TenantAdmin",
    "TenantNoEncontrado",
    "TransicionInvalida",
    "create_tenant",
    "dar_de_baja",
    "levantar_suspension",
    "listar_tenants",
    "obtener_tenant",
    "reactivar",
    "reenviar_invitacion",
    "suspender",
]
