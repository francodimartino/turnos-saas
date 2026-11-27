"""Cliente de email (Resend por HTTP) compartido por los servicios que envían avisos.

Este paquete no importa código de las apps ni lee el entorno.
"""

from .cliente import ClienteEmail
from .config import ConfigEmail
from .errores import ErrorEmail

__all__ = ["ClienteEmail", "ConfigEmail", "ErrorEmail"]
