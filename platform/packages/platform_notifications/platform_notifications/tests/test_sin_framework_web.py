"""El paquete de notificaciones no depende de un framework web: quien lo usa le pasa el programador."""

import ast
import os
import subprocess
import sys
from pathlib import Path

PAQUETE = Path(__file__).resolve().parents[1]
PROHIBIDOS = {"fastapi", "starlette"}


def _importados(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def test_el_paquete_no_importa_fastapi_ni_starlette():
    fuentes = [r for r in PAQUETE.rglob("*.py") if "tests" not in r.relative_to(PAQUETE).parts]
    assert PAQUETE / "envio.py" in fuentes
    infractores = {
        str(r.relative_to(PAQUETE)): sorted(_importados(r) & PROHIBIDOS) for r in fuentes if _importados(r) & PROHIBIDOS
    }
    assert not infractores, f"el paquete importa un framework web: {infractores}"


def test_el_detector_ve_los_imports_prohibidos(tmp_path):
    ejemplo = tmp_path / "x.py"
    ejemplo.write_text("from starlette.background import BackgroundTasks\nimport fastapi.routing\n", encoding="utf-8")
    assert _importados(ejemplo) >= {"fastapi", "starlette"}


def test_el_paquete_se_importa_con_el_framework_web_bloqueado():
    codigo = (
        "import sys\n"
        "sys.modules['fastapi'] = None\n"
        "sys.modules['starlette'] = None\n"
        "import platform_notifications\n"
        "assert platform_notifications.programar_envio\n"
    )
    entorno = {**os.environ, "PYTHONPATH": os.pathsep.join([str(PAQUETE.parent), *sys.path])}
    resultado = subprocess.run(  # noqa: S603 - ejecuta el propio intérprete con código fijo
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=False, env=entorno
    )
    assert resultado.returncode == 0, resultado.stderr
