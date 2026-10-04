"""Observabilidad común: logs JSON estructurados y Sentry sin PII (ARCHITECTURE.md §6.1, §6.3)."""

from .logs import EVENTOS_OBLIGATORIOS, configurar_logging, contexto_log, log_evento_obligatorio
from .pii import PII_CAMPOS
from .sentry import iniciar_sentry, sentry_before_send

__all__ = [
    "EVENTOS_OBLIGATORIOS",
    "PII_CAMPOS",
    "configurar_logging",
    "contexto_log",
    "iniciar_sentry",
    "log_evento_obligatorio",
    "sentry_before_send",
]
