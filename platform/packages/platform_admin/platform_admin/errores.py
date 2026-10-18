"""Errores de dominio de platform_admin.

El admin-backend los traduce a 4xx (404/409/422); un error que no sea de esta jerarquía es un fallo real (500).
Los mensajes no incluyen emails ni credenciales.
"""

from typing import Any


class ErrorDominioAdmin(Exception):
    """Base de los errores de dominio de platform_admin."""


class DatosTenantInvalidos(ErrorDominioAdmin, ValueError):
    """Los datos de alta (tenant_data o email) no son válidos. No se tocó la base ni la Auth Admin API."""


class SlugEnUso(ErrorDominioAdmin):
    """Ya existe un tenant con ese slug."""


class OwnerNoDisponible(ErrorDominioAdmin):
    """El email del owner ya tiene cuenta vinculada o invitación pendiente en un negocio.

    El mensaje es neutro: no dice en qué negocio ni distingue los dos casos. El alta se revierte entera.
    """

    def __init__(self) -> None:
        super().__init__("No se pudo dar de alta al owner con ese email")


class TenantNoEncontrado(ErrorDominioAdmin):
    """El tenant no existe (o no tiene owner, para la invitación)."""


class TransicionInvalida(ErrorDominioAdmin):
    """La acción no es válida para el estado actual del tenant (ARCHITECTURE.md §2.2)."""

    def __init__(self, accion: str, desde: str | None):
        self.accion = accion
        self.desde = desde
        super().__init__(f"No se puede {accion} un tenant en estado {desde!r}")


class PurgaEnCurso(TransicionInvalida):
    """El tenant está en `purging`: la purga ya empezó y no se puede reactivar, suspender ni dar de baja."""

    def __init__(self, accion: str):
        super().__init__(accion, "purging")

    def __str__(self) -> str:
        return f"No se puede {self.accion} el tenant: la purga ya está en curso"


class LimpiezaNoPendiente(ErrorDominioAdmin):
    """La limpieza de Calendar no existe para ese tenant y evento, o ya está resuelta o descartada."""


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


class ReduccionSinConfirmar(ErrorDominioAdmin):
    """Se intentó bajar un setting sensible (p. ej. el período de gracia) sin la confirmación explícita."""

    def __init__(self, clave: str, desde: Any, hasta: Any):
        self.clave = clave
        self.desde = desde
        self.hasta = hasta
        super().__init__(
            f"{clave}: bajar el valor de {desde} a {hasta} acorta el plazo para recuperar un negocio dado de baja; "
            "reenviá con confirmar_reduccion=true para confirmarlo"
        )


class IntegracionNoSoportada(ErrorDominioAdmin, ValueError):
    """El tipo de integración no se carga desde el panel (por-profesional o fuera del piloto)."""


class ErrorEliminacionCuenta(Exception):
    """Falló la eliminación de una cuenta por la Auth Admin API (red o respuesta que no sea 2xx/404). Sin PII."""
