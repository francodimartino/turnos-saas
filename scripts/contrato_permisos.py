"""Exporta el catálogo de permisos de Python a `contratos/permisos.json`.

Formato (JSON con claves ordenadas, sangría de 2 espacios, UTF-8 y salto de línea final):

    {
      "claves": ["approve_bookings", "delete_customers", ...],      # valores de public.permissions, ordenados
      "miembros": {"APROBAR_TURNOS": "approve_bookings", ...}       # nombre del miembro de Permiso -> clave
    }

`claves` es lo que viaja por la API (`permission_keys`) y el frontend lo usa para tipar la unión de permisos.

Uso:
    python scripts/contrato_permisos.py             # reescribe contratos/permisos.json
    python scripts/contrato_permisos.py --verificar # no escribe; código 1 si el archivo está desactualizado

El test `tests/test_contrato_permisos.py` corre la misma comparación en pytest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "contratos" / "permisos.json"


def _habilitar_importaciones() -> None:
    """Agrega las raíces de paquete de `[tool.pytest.ini_options].pythonpath` (el proyecto no se instala para esto)."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = texto.split("[tool.pytest.ini_options]")[1].split("pythonpath = [")[1].split("]")[0]
    for raiz in reversed(re.findall(r'"([^"]+)"', bloque)):
        ruta = str(RAIZ / raiz)
        if ruta not in sys.path:
            sys.path.insert(0, ruta)


def generar() -> str:
    """Contenido de `contratos/permisos.json` según el catálogo `platform_auth.Permiso`."""
    _habilitar_importaciones()
    from platform_auth import Permiso, claves_de_permisos

    contrato = {
        "claves": claves_de_permisos(),
        "miembros": {p.name: p.value for p in Permiso},
    }
    return json.dumps(contrato, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def desactualizado(archivo: Path = ARCHIVO) -> bool:
    return not archivo.exists() or archivo.read_text(encoding="utf-8") != generar()


def escribir(archivo: Path = ARCHIVO) -> None:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(generar(), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verificar", action="store_true", help="solo comparar; no escribe")
    args = parser.parse_args(argv)
    if args.verificar:
        if desactualizado():
            print("desactualizado: contratos/permisos.json (regenerar con `python scripts/contrato_permisos.py`)")
            return 1
        return 0
    escribir()
    print("escrito: contratos/permisos.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
