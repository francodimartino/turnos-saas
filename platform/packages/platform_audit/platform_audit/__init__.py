"""platform_audit — escritura consistente de `audit_log` desde cualquier servicio (ARCHITECTURE.md §4.3)."""

from platform_audit.registro import CLAVES_PII, MetadataConPII, registrar

__all__ = ["CLAVES_PII", "MetadataConPII", "registrar"]
