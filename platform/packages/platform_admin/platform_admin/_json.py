"""Decodificación de columnas jsonb: asyncpg las entrega como texto salvo que el pool registre un codec."""

import json
from typing import Any


def decodificar(valor: Any) -> Any:
    if isinstance(valor, (str, bytes, bytearray)):
        return json.loads(valor)
    return valor
