"""Errores de turnos_email. Ningún mensaje incluye destino, asunto, cuerpo, API key ni lo que respondió Resend."""


class ErrorEmail(Exception):
    """Falló un envío. El mensaje es fijo: solo el estado HTTP (si hubo respuesta) y si conviene reintentar."""

    def __init__(self, *, estado_http: int | None = None, retry_after: float | None = None) -> None:
        detalle = "sin respuesta del proveedor" if estado_http is None else f"estado {estado_http}"
        super().__init__(f"No se pudo enviar el email ({detalle})")
        self.estado_http = estado_http  # None: red o timeout
        self.retry_after = retry_after  # segundos que pidió esperar el proveedor, ya con tope

    @property
    def reintentable(self) -> bool:
        """Un 4xx distinto de 429 es permanente (dirección inválida, key rechazada): reintentar no lo arregla."""
        estado = self.estado_http
        return estado is None or estado == 429 or not 400 <= estado < 500
