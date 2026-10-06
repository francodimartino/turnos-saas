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
    (tmp_path / "pyproject.toml").write_text(
        (RAIZ / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "largo.py").write_text('x = "' + "a" * 130 + '"\n', encoding="utf-8")
    assert verificar.lint(tmp_path) != 0


def test_lint_pasa_con_codigo_limpio(tmp_path):
    verificar = _cargar_verificar()
    (tmp_path / "pyproject.toml").write_text(
        (RAIZ / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
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
    assert "pip install" in salida
