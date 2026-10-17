"""Errores de dominio de platform_admin.

El admin-backend los traduce a 4xx (404/409/422); un error que no sea de esta jerarquía es un fallo real (500).
Los mensajes no incluyen emails ni credenciales.
"""


class ErrorDominioAdmin(Exception):
    """Base de los errores de dominio de platform_admin."""


class DatosTenantInvalidos(ErrorDominioAdmin, ValueError):
    """Los datos de alta (tenant_data o email) no son válidos. No se tocó la base ni la Auth Admin API."""


class SlugEnUso(ErrorDominioAdmin):
    """Ya existe un tenant con ese slug."""


class TenantNoEncontrado(ErrorDominioAdmin):
    """El tenant no existe (o no tiene owner, para la invitación)."""


class TransicionInvalida(ErrorDominioAdmin):
    """La acción no es válida para el estado actual del tenant (ARCHITECTURE.md §2.2)."""

    def __init__(self, accion: str, desde: str | None):
        self.accion = accion
        self.desde = desde
        super().__init__(f"No se puede {accion} un tenant en estado {desde!r}")


class BotYaConectado(ErrorDominioAdmin):
    """El bot de Telegram (por su id numérico) ya está conectado a otro negocio. No identifica a cuál."""


class InvitacionNoPendiente(ErrorDominioAdmin):
    """El owner ya tiene cuenta vinculada: no hay invitación que reenviar."""


class ErrorInvitacion(Exception):
    """Falló la llamada a la Auth Admin API (red, 4xx/5xx, respuesta sin id). Sin PII ni credenciales."""


class DatosInvalidos(ErrorDominioAdmin, ValueError):
    """Parámetros inválidos de una operación del panel (filtros, facturación, config de integración).

    No se tocó la base. El mensaje nombra el campo o la clave, nunca el valor.
    """


class SettingInvalido(ErrorDominioAdmin, ValueError):
    """La clave de `platform_settings` no se puede editar o el valor no es válido para esa clave."""


class IntegracionNoSoportada(ErrorDominioAdmin, ValueError):
    """El tipo de integración no se carga desde el panel (por-profesional o fuera del piloto)."""


class ErrorEliminacionCuenta(Exception):
    """Falló la eliminación de una cuenta por la Auth Admin API (red o respuesta que no sea 2xx/404). Sin PII."""
