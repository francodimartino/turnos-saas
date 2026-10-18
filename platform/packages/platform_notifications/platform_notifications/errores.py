"""Errores de platform_notifications. Ningún mensaje incluye el token del bot, el texto enviado ni PII."""


class ErrorEnvio(Exception):
    """Falló un envío al proveedor. `codigo` y el mensaje son del proveedor (o de red), nunca el texto enviado."""

    def __init__(
        self, codigo: str, mensaje: str, *, estado_http: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje
        self.estado_http = estado_http  # None: fallo de red/timeout, sin respuesta del proveedor
        self.retry_after = retry_after  # segundos que pidió esperar el proveedor (429)

    @property
    def reintentable(self) -> bool:
        """Un 4xx distinto de 429 es permanente (p. ej. 403 por bot bloqueado): reintentar no lo arregla.

        Red, timeouts, 5xx y 429 se reintentan. Un timeout se reintenta aunque Telegram pudiera haber
        recibido el mensaje: se prefiere un posible duplicado a perder un aviso de turno.
        """
        estado = self.estado_http
        return estado is None or estado == 429 or not 400 <= estado < 500


class ErrorCredencial(Exception):
    """No se pudo obtener la credencial del bot del tenant. Nunca incluye el contenido del secret."""


class IntegracionNoConfigurada(ErrorCredencial):
    """El tenant no tiene la integración del bot."""


class CredencialInvalida(ErrorCredencial):
    """El secret de la integración no tiene la forma esperada (`{bot_token, webhook_secret}`)."""
