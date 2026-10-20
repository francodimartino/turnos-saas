"""Reglas de `tenants.settings` compartidas por los paneles (dashboard del local y alta de tenant del admin).

Son reglas de turnos (claves y límites de reserva, datos del local para el bot), por eso viven con el dominio y no
dentro de un canal. Las claves conocidas se validan por tipo, largo y rango; las desconocidas se aceptan dentro de
los topes de cantidad de claves y de bytes. Ningún valor puede contener floats no finitos. Los errores son
`ValueError` con un mensaje que nombra la clave y nunca el valor.

`platform_admin` (que no puede importar `apps/shared`) no valida acá: lo hace la ruta del admin-backend antes de
llamarlo.
"""

import json
from typing import Any

from turnos_dominio.valores_json import contiene_no_finito

MAX_CLAVES_SETTINGS = 50
MAX_BYTES_SETTINGS = 20_000
CLAVE_HORAS_APROBACION = "booking_approval_timeout_hours"
CLAVE_CAMPOS_EXTRA = "end_customer_extra_fields"

MAX_LARGO_TEXTO_LOCAL = 500
MAX_LARGO_HORARIOS = 2000
MAX_LINEAS_HORARIOS = 30
MAX_CAMPOS_EXTRA = 20
MAX_LARGO_CLAVE_EXTRA = 64
MAX_LARGO_ETIQUETA_EXTRA = 100

# Rangos enteros de los límites de reserva; son los mismos que hace cumplir el trigger de la base (migración
# `...001700`), que sigue siendo la última palabra. Acá solo se adelanta el rechazo con un mensaje que nombra la clave.
RANGOS_ENTEROS_SETTINGS: dict[str, tuple[int, int]] = {
    CLAVE_HORAS_APROBACION: (1, 168),
    "booking_max_active_per_customer": (1, 50),
    "booking_max_per_hour": (1, 60),
    "booking_min_notice_minutes": (0, 1440),
}


def _texto(clave: str, valor: Any, maximo: int) -> None:
    if not isinstance(valor, str) or len(valor) > maximo:
        raise ValueError(f"{clave} debe ser un texto de hasta {maximo} caracteres")


def _horarios(clave: str, valor: Any) -> None:
    if isinstance(valor, str):
        _texto(clave, valor, MAX_LARGO_HORARIOS)
        return
    if not isinstance(valor, list) or len(valor) > MAX_LINEAS_HORARIOS:
        raise ValueError(f"{clave} debe ser un texto o una lista de hasta {MAX_LINEAS_HORARIOS} textos")
    for linea in valor:
        if not isinstance(linea, str) or len(linea) > MAX_LARGO_TEXTO_LOCAL:
            raise ValueError(f"{clave} debe ser una lista de textos de hasta {MAX_LARGO_TEXTO_LOCAL} caracteres")


def _campos_extra(clave: str, valor: Any) -> None:
    if not isinstance(valor, list) or len(valor) > MAX_CAMPOS_EXTRA:
        raise ValueError(f"{clave} debe ser una lista de hasta {MAX_CAMPOS_EXTRA} campos")
    nombre: Any
    etiqueta: Any
    for campo in valor:
        if isinstance(campo, str):
            nombre, etiqueta = campo, None
        elif isinstance(campo, dict):
            nombre, etiqueta = campo.get("key"), campo.get("label")
        else:
            raise ValueError(f"{clave} solo admite textos u objetos con 'key'")
        if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > MAX_LARGO_CLAVE_EXTRA:
            raise ValueError(f"{clave}: cada campo necesita una clave de 1 a {MAX_LARGO_CLAVE_EXTRA} caracteres")
        if etiqueta is not None and (not isinstance(etiqueta, str) or len(etiqueta) > MAX_LARGO_ETIQUETA_EXTRA):
            raise ValueError(f"{clave}: la etiqueta debe ser un texto de hasta {MAX_LARGO_ETIQUETA_EXTRA} caracteres")


def validar_valor_de_setting(clave: str, valor: Any) -> None:
    """Valida tipo y largo de una clave conocida de `tenants.settings`; las desconocidas pasan (van por los topes).

    Levanta `ValueError` con un mensaje que nombra la clave, nunca el valor. `None` (borrar la clave) siempre es válido.
    """
    if valor is None:
        return
    if clave in RANGOS_ENTEROS_SETTINGS:
        minimo, maximo = RANGOS_ENTEROS_SETTINGS[clave]
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError(f"{clave} debe ser un número entero")
        if not minimo <= valor <= maximo:
            raise ValueError(f"{clave} debe estar entre {minimo} y {maximo}")
    elif clave in ("local_direccion", "local_contacto"):
        _texto(clave, valor, MAX_LARGO_TEXTO_LOCAL)
    elif clave == "local_horarios":
        _horarios(clave, valor)
    elif clave == CLAVE_CAMPOS_EXTRA:
        _campos_extra(clave, valor)


def validar_settings(settings: dict[str, Any]) -> None:
    """Valida un `settings` completo: cantidad y largo de claves, no finitos, cada clave conocida y tope de bytes."""
    if len(settings) > MAX_CLAVES_SETTINGS:
        raise ValueError(f"demasiadas claves (máximo {MAX_CLAVES_SETTINGS})")
    for clave, valor in settings.items():
        if not clave.strip() or len(clave) > 64:
            raise ValueError("las claves deben tener entre 1 y 64 caracteres")
        if contiene_no_finito(valor):
            raise ValueError(f"{clave} no admite valores no finitos (Infinity, NaN)")
        validar_valor_de_setting(clave, valor)
    if len(json.dumps(settings)) > MAX_BYTES_SETTINGS:
        raise ValueError("los settings son demasiado grandes")
