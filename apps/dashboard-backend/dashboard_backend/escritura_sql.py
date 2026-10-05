"""Armado de UPDATE parciales (PATCH) con parámetros; los nombres de columna salen del código, no del cliente."""

import re
from typing import Any

from fastapi import HTTPException

_COLUMNA = re.compile(r"^[a-z][a-z_]*$")


def armar_set(cambios: dict[str, Any], *, primer_parametro: int, expresiones: dict[str, str] | None = None):
    """Devuelve `("col = $3, otra = $4", [valores])`.

    `expresiones` permite una expresión SQL propia para una columna, con `{p}` en el lugar del parámetro (por
    ejemplo para castear). Las claves de `cambios` deben ser nombres de columna conocidos por el código llamador.
    """
    expresiones = expresiones or {}
    partes: list[str] = []
    valores: list[Any] = []
    for columna, valor in cambios.items():
        if not _COLUMNA.match(columna):
            raise ValueError(f"nombre de columna inválido: {columna!r}")
        parametro = f"${primer_parametro + len(valores)}"
        partes.append(f"{columna} = {expresiones.get(columna, '{p}').format(p=parametro)}")
        valores.append(valor)
    return ", ".join(partes), valores


def no_encontrado(que: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"codigo": "no_encontrado", "mensaje": f"{que} no existe."})
