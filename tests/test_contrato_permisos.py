"""El catálogo de permisos es la única fuente en Python y `contratos/permisos.json` coincide con él.

- Un test estático falla si el código de apps/ o platform/ escribe una clave de permiso fuera del catálogo.
- El archivo versionado se compara con lo que genera `scripts/contrato_permisos.py`.
"""

import ast
import importlib.util
import json
from pathlib import Path

from platform_auth import Permiso

RAIZ = Path(__file__).resolve().parent.parent
COMANDO = "python scripts/contrato_permisos.py"
CATALOGO = RAIZ / "platform" / "packages" / "platform_auth" / "platform_auth" / "catalogo_permisos.py"
CLAVES = {p.value for p in Permiso}


def _cargar_generador():
    spec = importlib.util.spec_from_file_location("contrato_permisos", RAIZ / "scripts" / "contrato_permisos.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _fuentes_de_produccion():
    for raiz in ("apps", "platform"):
        for ruta in (RAIZ / raiz).rglob("*.py"):
            partes = ruta.relative_to(RAIZ).parts
            if "tests" in partes or "node_modules" in partes or ".venv" in partes or ruta == CATALOGO:
                continue
            yield ruta


def test_el_catalogo_tiene_las_claves_esperadas():
    assert {
        "view_bookings",
        "approve_bookings",
        "edit_prices",
        "manage_staff",
        "manage_customers",
        "delete_customers",
        "manage_schedule",
    } == CLAVES


def test_ningun_literal_de_permiso_fuera_del_catalogo():
    fuera = []
    for ruta in _fuentes_de_produccion():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and nodo.value in CLAVES:
                fuera.append(f"{ruta.relative_to(RAIZ).as_posix()}:{nodo.lineno} {nodo.value!r}")
    assert not fuera, "claves de permiso escritas fuera del catálogo (usar `platform_auth.Permiso`): " + ", ".join(
        fuera
    )


def test_el_contrato_versionado_esta_al_dia():
    archivo = RAIZ / "contratos" / "permisos.json"
    assert archivo.exists(), f"falta contratos/permisos.json: generarlo con `{COMANDO}`"
    assert archivo.read_text(encoding="utf-8") == _cargar_generador().generar(), (
        f"contratos/permisos.json está desactualizado respecto del catálogo: regenerarlo con `{COMANDO}` y commitear"
    )


def test_el_contrato_exporta_claves_ordenadas_y_miembros():
    contrato = json.loads(_cargar_generador().generar())
    assert contrato["claves"] == sorted(CLAVES)
    assert contrato["miembros"] == {p.name: p.value for p in Permiso}


def test_el_generador_detecta_un_archivo_desactualizado(tmp_path):
    generador = _cargar_generador()
    archivo = tmp_path / "permisos.json"
    assert generador.desactualizado(archivo)
    archivo.write_text("{}\n", encoding="utf-8")
    assert generador.desactualizado(archivo)
    generador.escribir(archivo)
    assert not generador.desactualizado(archivo)


def test_permiso_se_comporta_como_un_texto():
    p = Permiso.VER_TURNOS
    assert p == "view_bookings"
    assert str(p) == "view_bookings"
    assert f"{p}" == "view_bookings"
    assert json.dumps({"k": p}) == '{"k": "view_bookings"}'
    assert Permiso("view_bookings") is p
