"""Ningún código del bot indexa la sesión con un literal: el estado vive en los modelos tipados y versionados."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
INDEXACION = re.compile(r"""sesion\s*\[\s*["']""")


def test_no_se_indexa_la_sesion_con_literales_fuera_de_los_modelos():
    ofensores = []
    for archivo in RAIZ.rglob("*.py"):
        if archivo.name == "modelo_sesion.py" or archivo == Path(__file__).resolve():
            continue
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if INDEXACION.search(linea) and "pool.sesiones" not in linea:
                ofensores.append(f"{archivo.relative_to(RAIZ)}:{numero}")
    assert ofensores == []
