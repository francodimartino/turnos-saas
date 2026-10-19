"""Parsers de configuración de arranque compartidos por los backends FastAPI.

Leen un `Mapping` del entorno (nunca `os.environ` directo), validan y lanzan `ConfigInvalida` con un mensaje que
nombra la variable. Cada servicio compone su `Config` con estas piezas; las variables propias de un servicio y sus
valores por defecto se quedan en su `config.py`.

Este paquete no importa código de las apps.
"""

from .parsers import (
    ConfigInvalida,
    booleano,
    encabezado_ip,
    entero,
    entero_o_none,
    obligatoria,
    samesite,
    texto_opcional,
    validar_origen,
    validar_url_supabase,
)

__all__ = [
    "ConfigInvalida",
    "booleano",
    "encabezado_ip",
    "entero",
    "entero_o_none",
    "obligatoria",
    "samesite",
    "texto_opcional",
    "validar_origen",
    "validar_url_supabase",
]
