"""Cliente de Supabase Storage por HTTP para el logo de cada negocio (ARCHITECTURE.md §2.12).

La clave de servicio la lee la config de cada servicio y la inyecta acá; el paquete no toca el entorno.
"""

from turnos_almacenamiento.cliente import (
    EXTENSIONES,
    GRACIA_LIMPIEZA_SEG,
    TAMANO_MAXIMO_BYTES,
    AlmacenamientoNoDisponible,
    AlmacenamientoSinRespuesta,
    ArchivoDemasiadoGrande,
    ClienteAlmacenamiento,
    ConfiguracionAlmacenamientoInvalida,
    ErrorAlmacenamiento,
    TipoNoPermitido,
    detectar_formato,
    ruta_logo,
    url_publica_logo,
)

__all__ = [
    "EXTENSIONES",
    "TAMANO_MAXIMO_BYTES",
    "GRACIA_LIMPIEZA_SEG",
    "AlmacenamientoNoDisponible",
    "AlmacenamientoSinRespuesta",
    "ArchivoDemasiadoGrande",
    "ClienteAlmacenamiento",
    "ConfiguracionAlmacenamientoInvalida",
    "ErrorAlmacenamiento",
    "TipoNoPermitido",
    "detectar_formato",
    "ruta_logo",
    "url_publica_logo",
]
