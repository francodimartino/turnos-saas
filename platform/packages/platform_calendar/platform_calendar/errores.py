"""Errores de platform_calendar. Ningún mensaje incluye tokens, secrets, nombres ni teléfonos."""


class ErrorCalendar(Exception):
    """Falla al hablar con el calendario."""


class ErrorConfiguracionCalendar(ErrorCalendar):
    """Falta o es inválida la configuración OAuth del proyecto Google (variables de entorno)."""


class ErrorEstadoOAuth(ErrorCalendar):
    """El parámetro `state` del flujo OAuth es inválido, fue alterado, venció o es de otro tenant."""


class ErrorOAuth(ErrorCalendar):
    """Google rechazó el intercambio de código o el refresco de token."""


class ErrorTokenVencido(ErrorOAuth):
    """El refresh_token ya no sirve (revocado o expirado): el profesional tiene que reconectar Google."""


class ErrorConexionEliminada(ErrorCalendar):
    """La integración de calendario del profesional no existe (se eliminó o es de otro tenant)."""


class ErrorRedCalendar(ErrorCalendar):
    """No se pudo contactar a Google (red, timeout)."""


class ErrorApiCalendar(ErrorCalendar):
    """La Calendar API respondió con un error HTTP."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Google Calendar respondió {status}")
        self.status = status
