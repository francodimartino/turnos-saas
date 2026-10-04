"""Errores de platform_notifications. Ningún mensaje incluye el token del bot, el texto enviado ni PII."""


class ErrorEnvio(Exception):
    """Falló un envío al proveedor. `codigo` y el mensaje son del proveedor (o de red), nunca el texto enviado."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


class ErrorCredencial(Exception):
    """No se pudo obtener la credencial del bot del tenant. Nunca incluye el contenido del secret."""


class IntegracionNoConfigurada(ErrorCredencial):
    """El tenant no tiene la integración del bot."""


class CredencialInvalida(ErrorCredencial):
    """El secret de la integración no tiene la forma esperada (`{bot_token, webhook_secret}`)."""
