"""El chequeo de contratos y de tipos de la API corre en el CI y en la verificación local."""

import importlib.util
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


def _cargar_verificar():
    spec = importlib.util.spec_from_file_location("verificar", RAIZ / "scripts" / "verificar.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_ci_verifica_los_contratos_en_el_job_de_pytest():
    ci = _leer(".github/workflows/ci.yml")
    job = ci[ci.index("  python:") : ci.index("  integracion:")]
    assert "python scripts/contrato_openapi.py --verificar" in job
    assert "python scripts/contrato_permisos.py --verificar" in job
    assert "npm run api:generar" in job  # el mensaje dice cómo regenerar


def test_el_ci_verifica_los_tipos_de_cada_frontend():
    ci = _leer(".github/workflows/ci.yml")
    job = ci[ci.index("  frontends:") : ci.index("  aislamiento-rls:")]
    assert "npm run api:verificar" in job
    assert "admin-frontend-template" in job  # la matriz incluye los tres frontends


def test_el_ci_dispara_los_frontends_si_cambian_el_contrato_o_los_backends():
    ci = _leer(".github/workflows/ci.yml")
    linea = next(
        ln for ln in ci.splitlines() if "grep -Eq" in ln and "admin-frontend-template" in ln and "package" in ln
    )
    for patron in ("^contratos/", "^apps/(dashboard|admin)-backend/"):
        assert patron in linea


def test_verificar_corre_contratos_y_tipos_de_la_api():
    fuente = _leer("scripts/verificar.py")
    cuerpo = fuente[fuente.index("def tests()") : fuente.index("def build()")]
    assert cuerpo.index("pytest") < cuerpo.index("contratos()") < cuerpo.index("tests_frontends()")
    assert "contrato_openapi.py" in fuente and "contrato_permisos.py" in fuente
    assert '"api:verificar"' in fuente
    assert fuente.count("verificar_tipos_api(") >= 3  # definición + modos tests y build


def test_los_tres_frontends_tienen_api_verificar():
    for app in ("apps/dashboard-frontend", "apps/admin-frontend", "platform/admin-frontend-template"):
        assert re.search(r'"api:verificar":', _leer(f"{app}/package.json")), app


def test_contratos_omite_el_openapi_con_otro_python(monkeypatch, capsys):
    verificar = _cargar_verificar()
    llamados = []
    monkeypatch.setattr(verificar, "correr", lambda cmd, cwd=None: llamados.append(cmd[1]) or 0)
    monkeypatch.setattr(sys, "version_info", (3, 10, 6, "final", 0))
    assert verificar.contratos() == 0
    assert llamados == ["scripts/contrato_permisos.py"]
    salida = capsys.readouterr().out
    assert "Contrato OpenAPI OMITIDO" in salida and "3.12" in salida


def test_contratos_corre_el_openapi_con_el_python_del_lock(monkeypatch):
    verificar = _cargar_verificar()
    llamados = []
    monkeypatch.setattr(verificar, "correr", lambda cmd, cwd=None: llamados.append(cmd[1]) or 0)
    monkeypatch.setattr(sys, "version_info", (3, 12, 1, "final", 0))
    assert verificar.contratos() == 0
    assert llamados == ["scripts/contrato_permisos.py", "scripts/contrato_openapi.py"]


def test_agents_documenta_el_flujo_de_contratos():
    agents = _leer("AGENTS.md")
    assert "api:verificar" in agents and "Python 3.12" in agents
    assert "OMITIDO" in agents
