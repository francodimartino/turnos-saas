"""Errores de platform_tenancy."""


class ContextoTenantInvalido(ValueError):
    """El tenant_id o el actor_id no es un UUID válido (o falta el tenant_id). No se toca la base."""
