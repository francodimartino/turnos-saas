"""Cliente de Supabase Storage por HTTP para el logo de cada negocio (ARCHITECTURE.md §2.12).

La clave de servicio la lee la config de cada servicio y la inyecta acá; el paquete no toca el entorno.
"""

from turnos_almacenamiento.cliente import (
    EXTENSIONES,
    TAMANO_MAXIMO_BYTES,
    AlmacenamientoNoDisponible,
    ArchivoDemasiadoGrande,
    ClienteAlmacenamiento,
    ConfiguracionAlmacenamientoInvalida,
    ErrorAlmacenamiento,
    TipoNoPermitido,
    detectar_formato,
    ruta_logo,
)

__all__ = [
    "EXTENSIONES",
    "TAMANO_MAXIMO_BYTES",
    "AlmacenamientoNoDisponible",
    "ArchivoDemasiadoGrande",
    "ClienteAlmacenamiento",
    "ConfiguracionAlmacenamientoInvalida",
    "ErrorAlmacenamiento",
    "TipoNoPermitido",
    "detectar_formato",
    "ruta_logo",
]
