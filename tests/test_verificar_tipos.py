"""mypy en el CI y en la verificación local: se invoca después de ruff, falla si falta y no se saltea."""
import importlib.util
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_verificar():
    spec = importlib.util.spec_from_file_location("verificar", RAIZ / "scripts" / "verificar.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


def test_el_ci_corre_mypy_despues_de_ruff():
    ci = _leer(".github/workflows/ci.yml")
    assert re.search(r"run: ruff check \.\s+- name: [^\n]*\n\s+run: mypy \.", ci)


def test_verificar_corre_mypy_despues_de_ruff():
    fuente = _leer("scripts/verificar.py")
    cuerpo = fuente[fuente.index("def tests()"):]
    assert cuerpo.index("lint()") < cuerpo.index("tipos()") < cuerpo.index("pytest")


def test_agents_lista_el_comando_de_mypy():
    assert "`mypy .`" in _leer("AGENTS.md")


def test_mypy_esta_en_dev_y_en_los_dos_locks_solo_el_de_dev():
    assert re.search(r'dev = \[[^\]]*"mypy"', _leer("pyproject.toml"))
    assert re.search(r"^mypy==\S+", _leer("requirements-dev.lock"), re.M)
    assert not re.search(r"^mypy==", _leer("requirements.lock"), re.M)


def test_mypy_usa_las_mismas_rutas_que_pytest():
    pyproject = _leer("pyproject.toml")

    def rutas(patron: str) -> list[str]:
        bloque = re.search(patron, pyproject, re.S)
        assert bloque is not None
        return re.findall(r'"([^"]+)"', bloque.group(1))

    de_mypy = rutas(r"mypy_path = \[(.*?)\]")
    assert de_mypy and de_mypy == rutas(r"pythonpath = \[(.*?)\]")


def test_tipos_falla_con_un_error_de_tipos(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\npython_version = \"3.12\"\n", encoding="utf-8")
    (tmp_path / "mal.py").write_text("def f(x: int) -> str:\n    return x\n", encoding="utf-8")
    assert verificar.tipos(tmp_path) != 0


def test_tipos_pasa_con_codigo_limpio(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\npython_version = \"3.12\"\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("def f(x: int) -> int:\n    return x\n", encoding="utf-8")
    assert verificar.tipos(tmp_path) == 0


def test_tipos_sin_mypy_instalado_falla_con_mensaje(monkeypatch, capsys):
    verificar = _cargar_verificar()

    def falso_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(verificar.subprocess, "run", falso_run)
    assert verificar.tipos() == 1
    salida = capsys.readouterr().out
    assert "mypy no está instalado" in salida
    assert "pip install -r requirements-dev.lock && pip install --no-deps -e ." in salida
