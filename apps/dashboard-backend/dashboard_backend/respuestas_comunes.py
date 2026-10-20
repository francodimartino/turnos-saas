"""Tipos compartidos por los modelos de respuesta (`respuestas_<área>.py`): fechas y números con la salida de siempre.

Pydantic escribe un instante en UTC como `...Z` y un `Decimal` como texto; el dashboard escribe `...+00:00` y un número.
Estos tipos conservan esa salida, para que declarar un modelo de respuesta no cambie el JSON que ve el cliente.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema


def _fecha_iso(valor: datetime) -> str:
    return valor.isoformat()


def _numero(valor: Decimal) -> int | float:
    """Entero si no tiene decimales declarados (`Decimal('1500')`), flotante si los tiene (`Decimal('1500.50')`)."""
    return int(valor) if valor.as_tuple().exponent >= 0 else float(valor)  # type: ignore[operator]


FechaHora = Annotated[
    datetime,
    PlainSerializer(_fecha_iso, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
Numero = Annotated[
    Decimal,
    PlainSerializer(_numero, return_type=int | float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
