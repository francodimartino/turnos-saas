"""El paquete de notificaciones no depende de un framework web: quien lo usa le pasa el programador."""

import ast
import os
import subprocess
import sys
from pathlib import Path

PAQUETE = Path(__file__).resolve().parents[1]
JOBS = PAQUETE.parents[3] / "apps" / "jobs"
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


def _fuentes(raiz: Path) -> list[Path]:
    return [r for r in raiz.rglob("*.py") if "tests" not in r.relative_to(raiz).parts]


def _infractores(raiz: Path) -> dict[str, list[str]]:
    return {
        str(r.relative_to(raiz)): sorted(_importados(r) & PROHIBIDOS)
        for r in _fuentes(raiz)
        if _importados(r) & PROHIBIDOS
    }


def test_el_paquete_no_importa_fastapi_ni_starlette():
    assert PAQUETE / "envio.py" in _fuentes(PAQUETE)
    assert not _infractores(PAQUETE), f"el paquete importa un framework web: {_infractores(PAQUETE)}"


def test_los_jobs_no_importan_fastapi_ni_starlette():
    carpetas = ("jobs_comun", "jobs_purga", "jobs_vencimiento")
    assert all((JOBS / c).is_dir() for c in carpetas)
    infractores = {c: _infractores(JOBS / c) for c in carpetas if _infractores(JOBS / c)}
    assert not infractores, f"los jobs importan un framework web: {infractores}"


def test_el_detector_de_carpeta_marca_un_archivo_que_importa_fastapi(tmp_path):
    (tmp_path / "job.py").write_text("from fastapi import BackgroundTasks\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import fastapi\n", encoding="utf-8")
    assert _infractores(tmp_path) == {"job.py": ["fastapi"]}


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
