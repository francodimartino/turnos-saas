"""Protección de login compartida por los backends: limitador de intentos, IP real y verificación de Turnstile.

Ver los docstrings de `limitador` (variables `LOGIN_LIMITE_*` y sus valores por defecto), `ip` (`LOGIN_IP_HEADER`,
`LOGIN_PROXIES_CONFIABLES`) y `turnstile` (`TURNSTILE_SECRET_KEY`).
"""

from .ip import ConfigIp, ip_real
from .limitador import LimitadorIntentos, LimitesLogin, ResultadoIntento, limites_desde_entorno
from .turnstile import TurnstileNoDisponible, VerificadorTurnstile

__all__ = [
    "LimitadorIntentos",
    "LimitesLogin",
    "ResultadoIntento",
    "TurnstileNoDisponible",
    "VerificadorTurnstile",
    "ConfigIp",
    "ip_real",
    "limites_desde_entorno",
]
