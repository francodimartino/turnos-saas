"""Toda la configuración se lee y valida al arrancar: ninguna operación consulta el entorno.

Los paquetes de plataforma y los servicios (bot, backends, jobs) solo leen `os.environ` en sus módulos de
configuración (`config.py`) y en los puntos de entrada (`main.py`, `__main__.py`); el resto recibe los valores ya
decididos por parámetro.
"""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

CARPETAS = ("platform/packages", "apps/bot", "apps/admin-backend", "apps/dashboard-backend", "apps/jobs")
MODULOS_DE_CONFIGURACION = {"config.py", "main.py", "__main__.py"}

# Excepciones con motivo. Esta lista solo puede achicarse.
EXCEPCIONES = {
    # El cliente de Auth Admin se arma desde el entorno al invitar; pasa a recibir su configuración inyectada.
    "platform/packages/platform_auth/platform_auth/invitaciones.py",
}


def _es_de_prueba(ruta: Path) -> bool:
    partes = set(ruta.parts)
    return "tests" in partes or ruta.name.startswith("test_") or ruta.name == "conftest.py"


def _lecturas_de_entorno(ruta: Path) -> list[int]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    lineas = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Attribute)
            and nodo.attr in {"environ", "getenv", "environb"}
            and isinstance(nodo.value, ast.Name)
            and nodo.value.id == "os"
        ) or (
            isinstance(nodo, ast.ImportFrom)
            and nodo.module == "os"
            and any(a.name in {"environ", "getenv", "environb"} for a in nodo.names)
        ):
            lineas.append(nodo.lineno)
    return lineas


def _archivos_de_operacion() -> list[Path]:
    archivos = []
    for carpeta in CARPETAS:
        for ruta in sorted((RAIZ / carpeta).rglob("*.py")):
            if "node_modules" in ruta.parts or _es_de_prueba(ruta) or ruta.name in MODULOS_DE_CONFIGURACION:
                continue
            archivos.append(ruta)
    return archivos


def test_ninguna_operacion_lee_el_entorno_fuera_de_config_y_main():
    infractores = []
    for ruta in _archivos_de_operacion():
        relativa = ruta.relative_to(RAIZ).as_posix()
        if relativa in EXCEPCIONES:
            continue
        infractores += [f"{relativa}:{linea}" for linea in _lecturas_de_entorno(ruta)]
    assert infractores == [], "leen os.environ fuera de config.py / main.py: " + ", ".join(infractores)


def test_las_excepciones_siguen_leyendo_el_entorno():
    sobrantes = [e for e in EXCEPCIONES if not _lecturas_de_entorno(RAIZ / e)]
    assert sobrantes == [], f"ya no leen el entorno, borrarlas de EXCEPCIONES: {sobrantes}"


def test_el_detector_encuentra_las_lecturas(tmp_path):
    archivo = tmp_path / "x.py"
    archivo.write_text("import os\nfrom os import getenv\na = os.environ['A']\nb = os.getenv('B')\n", encoding="utf-8")
    assert _lecturas_de_entorno(archivo) == [2, 3, 4]
