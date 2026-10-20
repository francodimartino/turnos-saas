"""Utilidades sobre valores JSON recibidos del cliente."""

import math
from typing import Any


def contiene_no_finito(valor: Any) -> bool:
    """True si el valor, o algo anidado en listas y objetos, es un float `Infinity`, `-Infinity` o `NaN`.

    Python acepta esos literales al parsear JSON, pero Postgres (`jsonb`) no: sin este chequeo terminan en un 500.
    Es iterativo para no depender del límite de recursión con estructuras profundas.
    """
    pendientes = [valor]
    while pendientes:
        actual = pendientes.pop()
        if isinstance(actual, float):
            if not math.isfinite(actual):
                return True
        elif isinstance(actual, dict):
            pendientes.extend(actual.values())
        elif isinstance(actual, list | tuple):
            pendientes.extend(actual)
    return False
