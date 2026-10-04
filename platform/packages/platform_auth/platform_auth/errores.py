"""Errores de autenticación/autorización. Cada uno lleva el status HTTP que le corresponde.

Los mensajes son genéricos a propósito: no revelan por qué falló un token ni qué estado tiene el
tenant más allá de lo necesario para que el cliente sepa qué hacer.
"""


class ErrorAuth(Exception):
    status_code: int = 500
    codigo: str = "error_auth"
    mensaje: str = "Error de autenticación"

    def __init__(self, mensaje: str | None = None):
        super().__init__(mensaje or self.mensaje)
        if mensaje is not None:
            self.mensaje = mensaje


class NoAutenticado(ErrorAuth):
    """401: token ausente, malformado, expirado, de otra audiencia/emisor o sin identidad utilizable."""

    status_code = 401
    codigo = "no_autenticado"
    mensaje = "Sesión inválida o expirada"


class Prohibido(ErrorAuth):
    """403: la identidad es válida pero no puede usar este endpoint."""

    status_code = 403
    codigo = "prohibido"
    mensaje = "Acceso denegado"


class MFARequerida(Prohibido):
    codigo = "mfa_requerida"
    mensaje = "Se requiere verificación en dos pasos (MFA)"


class TenantNoActivo(Prohibido):
    codigo = "tenant_no_activo"
    mensaje = "El negocio no está activo"


class StaffInactivo(Prohibido):
    codigo = "staff_inactivo"
    mensaje = "El usuario no tiene acceso a este negocio"


class NoEsPlatformAdmin(Prohibido):
    codigo = "no_es_platform_admin"
    mensaje = "Se requiere ser administrador de la plataforma"


class ConfigJWTInvalida(ValueError):
    """La configuración de verificación de JWT es inconsistente o insegura (falla al arrancar, no por request)."""
