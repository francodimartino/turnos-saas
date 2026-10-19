"""El dominio de turnos no depende de ningún canal ni de un framework web."""

import ast
from pathlib import Path

PAQUETE = Path(__file__).resolve().parents[1]
PROHIBIDOS = {"fastapi", "bot_app", "dashboard_backend", "admin_backend"}


def _importados(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def test_el_dominio_no_importa_canales_ni_fastapi():
    fuentes = [r for r in PAQUETE.rglob("*.py") if "tests" not in r.relative_to(PAQUETE).parts]
    assert PAQUETE / "huecos.py" in fuentes
    infractores = {
        str(r.relative_to(PAQUETE)): sorted(_importados(r) & PROHIBIDOS) for r in fuentes if _importados(r) & PROHIBIDOS
    }
    assert not infractores, f"el dominio importa lo que no debe: {infractores}"


def test_el_detector_ve_los_imports_prohibidos(tmp_path):
    ejemplo = tmp_path / "x.py"
    ejemplo.write_text("import fastapi.routing\nfrom bot_app.reserva import flujo\n", encoding="utf-8")
    assert _importados(ejemplo) >= {"fastapi", "bot_app"}
