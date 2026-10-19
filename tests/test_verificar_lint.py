"""El lint de scripts/verificar.py: falla ante E501 y ante ruff ausente (no se saltea)."""

import importlib.util
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_verificar():
    spec = importlib.util.spec_from_file_location("verificar", RAIZ / "scripts" / "verificar.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_lint_falla_con_linea_de_mas_de_120_caracteres(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text((RAIZ / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "largo.py").write_text('x = "' + "a" * 130 + '"\n', encoding="utf-8")
    assert verificar.lint(tmp_path) != 0


def test_lint_pasa_con_codigo_limpio(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text((RAIZ / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert verificar.lint(tmp_path) == 0


def test_lint_sin_ruff_instalado_falla_con_mensaje(monkeypatch, capsys):
    verificar = _cargar_verificar()

    def falso_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, b"", b"")

    monkeypatch.setattr(verificar.subprocess, "run", falso_run)
    assert verificar.lint() == 1
    salida = capsys.readouterr().out
    assert "ruff no está instalado" in salida
    assert "pip install -r requirements-dev.lock && pip install --no-deps -e ." in salida
    assert "-e .[dev]" not in salida


def test_lint_falla_con_codigo_sin_formatear(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text((RAIZ / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "feo.py").write_text("x   =   1\n", encoding="utf-8")
    assert verificar.lint(tmp_path) != 0


def test_verificar_y_el_ci_corren_ruff_format_check():
    assert '"format", "--check", "."' in (RAIZ / "scripts" / "verificar.py").read_text(encoding="utf-8")
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.index("ruff check .") < ci.index("ruff format --check .") < ci.index("mypy")
