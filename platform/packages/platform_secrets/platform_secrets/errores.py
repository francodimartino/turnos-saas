"""Errores de platform_secrets. Ningún mensaje incluye valores de secrets ni detalle interno de la base."""


class ErrorSecretos(Exception):
    """Falla al leer o guardar un secret."""


class AccesoSecretoDenegado(ErrorSecretos):
    """El rol o el tenant de la conexión no puede hacer esa operación (o la integración no existe).

    Inexistente y de otro tenant son indistinguibles a propósito: no revela qué ids existen.
    """
