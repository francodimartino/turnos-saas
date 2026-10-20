"""El contrato OpenAPI versionado de los backends coincide con el que generan las rutas.

El esquema sale de `app.openapi()` sobre apps armadas con piezas falsas: `/openapi.json`, `/docs` y `/redoc`
siguen apagados en las apps reales. Si una ruta o un modelo cambia, hay que regenerar los archivos de `contratos/`.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
COMANDO = "python scripts/contrato_openapi.py"


def _cargar_generador():
    spec = importlib.util.spec_from_file_location("contrato_openapi", RAIZ / "scripts" / "contrato_openapi.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def generado():
    return _cargar_generador().generar()


def test_se_generan_los_contratos_de_los_dos_backends(generado):
    assert sorted(generado) == ["admin-backend.openapi.json", "dashboard-backend.openapi.json"]
    for texto in generado.values():
        esquema = json.loads(texto)
        assert esquema["openapi"].startswith("3.")
        assert esquema["paths"]


def test_el_contrato_generado_es_estable(generado):
    assert _cargar_generador().generar() == generado


@pytest.mark.skipif(
    sys.version_info[:2] != (3, 12),
    reason="el contrato se genera con el entorno del lock (Python 3.12); lo cubre el CI",
)
@pytest.mark.parametrize("nombre", ["admin-backend.openapi.json", "dashboard-backend.openapi.json"])
def test_el_contrato_versionado_esta_al_dia(generado, nombre):
    archivo = RAIZ / "contratos" / nombre
    assert archivo.exists(), f"falta {archivo.relative_to(RAIZ).as_posix()}: generarlo con `{COMANDO}`"
    versionado = archivo.read_text(encoding="utf-8")
    assert versionado == generado[nombre], (
        f"contratos/{nombre} está desactualizado respecto de las rutas: regenerarlo con `{COMANDO}` y commitear"
    )


def test_el_script_no_expone_el_esquema_de_las_apps_reales():
    from admin_backend.tests.soporte import app_de_prueba as admin
    from dashboard_backend.tests.soporte import app_de_prueba as dashboard

    for app in (admin()[0], dashboard()[0]):
        assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_el_generador_detecta_un_archivo_desactualizado(tmp_path):
    generador = _cargar_generador()
    (tmp_path / "admin-backend.openapi.json").write_text("{}\n", encoding="utf-8")
    assert generador.desactualizados(tmp_path) == ["admin-backend.openapi.json", "dashboard-backend.openapi.json"]
    generador.escribir(tmp_path)
    assert generador.desactualizados(tmp_path) == []
