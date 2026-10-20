"""Genera el contrato OpenAPI de los backends sin exponerlo.

Arma `dashboard-backend` y `admin-backend` con piezas falsas (pool, Supabase Auth y verificador de prueba: sin red, sin
base y sin variables de entorno) y vuelca `app.openapi()` en `contratos/<backend>.openapi.json`. Las apps reales
siguen sin servir `/openapi.json`, `/docs` ni `/redoc`: el esquema se obtiene en proceso, no por HTTP.

Formato: JSON con claves ordenadas, sangría de 2 espacios, UTF-8 y salto de línea final (la salida es estable: el
mismo código produce el mismo archivo byte a byte; sin la descripción de cada modelo, que viene de su docstring).

Uso:
    python scripts/contrato_openapi.py             # reescribe contratos/*.openapi.json
    python scripts/contrato_openapi.py --verificar # no escribe; código 1 si algún archivo está desactualizado

El test `tests/test_contrato_openapi.py` corre la misma comparación en pytest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA_CONTRATOS = RAIZ / "contratos"


def _habilitar_importaciones() -> None:
    """Agrega las raíces de paquete de `[tool.pytest.ini_options].pythonpath` (el proyecto no se instala para esto)."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = texto.split("[tool.pytest.ini_options]")[1].split("pythonpath = [")[1].split("]")[0]
    for raiz in reversed(re.findall(r'"([^"]+)"', bloque)):
        ruta = str(RAIZ / raiz)
        if ruta not in sys.path:
            sys.path.insert(0, ruta)


def _normalizar(esquema: dict) -> dict:
    """Quita la descripción de cada modelo: sale de su docstring y según la versión de Pydantic aparece o no."""
    for modelo in esquema.get("components", {}).get("schemas", {}).values():
        modelo.pop("description", None)
    return esquema


def _a_texto(esquema: dict) -> str:
    esquema = _normalizar(esquema)
    return json.dumps(esquema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generar() -> dict[str, str]:
    """Nombre de archivo -> contenido del contrato de cada backend, armados en memoria."""
    _habilitar_importaciones()
    from admin_backend.tests.soporte import app_de_prueba as app_admin
    from dashboard_backend.tests.soporte import app_de_prueba as app_dashboard

    return {
        "admin-backend.openapi.json": _a_texto(app_admin()[0].openapi()),
        "dashboard-backend.openapi.json": _a_texto(app_dashboard()[0].openapi()),
    }


def desactualizados(carpeta: Path = CARPETA_CONTRATOS) -> list[str]:
    """Archivos de `carpeta` que faltan o difieren de lo que generan las rutas."""
    esperado = generar()
    return sorted(
        nombre
        for nombre, texto in esperado.items()
        if not (carpeta / nombre).exists() or (carpeta / nombre).read_text(encoding="utf-8") != texto
    )


def escribir(carpeta: Path = CARPETA_CONTRATOS) -> list[str]:
    carpeta.mkdir(parents=True, exist_ok=True)
    generado = generar()
    for nombre, texto in generado.items():
        (carpeta / nombre).write_text(texto, encoding="utf-8", newline="\n")
    return sorted(generado)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verificar", action="store_true", help="solo comparar; no escribe")
    args = parser.parse_args(argv)
    if args.verificar:
        sobrantes = desactualizados()
        for nombre in sobrantes:
            print(f"desactualizado: contratos/{nombre} (regenerar con `python scripts/contrato_openapi.py`)")
        return 1 if sobrantes else 0
    for nombre in escribir():
        print(f"escrito: contratos/{nombre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
