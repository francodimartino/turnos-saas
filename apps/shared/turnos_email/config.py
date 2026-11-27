"""Configuración del cliente de email. La arma el servicio que lo usa al arrancar (ver `turnos_config`)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfigEmail:
    api_key: str = field(repr=False)  # fuera del repr: un log de la config no debe revelarla
    remitente: str
    url_base: str = "https://api.resend.com"
    timeout_conexion: float = 5.0
    timeout_total: float = 15.0
    max_reintentos: int = 2
    tope_espera: float = 10.0  # menor que timeout_total: al menos una espera entra en el plazo
