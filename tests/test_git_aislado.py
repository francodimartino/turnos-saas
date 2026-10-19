"""Los tests que lanzan git lo hacen por `git_aislado`: sin fsmonitor y sin GIT_* heredado.

Con `core.fsmonitor=true` en la config global, cada repo temporal dejaba vivo un
`git fsmonitor--daemon` (llegaron a ser miles). Mecanismos cubiertos: el entorno
(`GIT_CONFIG_COUNT`), la opcion `-c` del comando, un test estatico que impide lanzar git
sin el helper y una prueba contra una config global con fsmonitor activo.
"""

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import git_aislado

RAIZ = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")
requiere_git = pytest.mark.skipif(not GIT, reason="requiere git")

_LANZADORES = {"run", "Popen", "check_output", "check_call", "call"}
# El helper es el unico que puede invocar git directamente.
_EXCLUIDOS = {"git_aislado.py"}


def _es_git(nodo: ast.expr) -> bool:
    """True si el primer elemento del comando es git: literal "git", nombre GIT/git o shutil.which("git")."""
    if isinstance(nodo, ast.Constant):
        return nodo.value == "git"
    if isinstance(nodo, ast.Name):
        return nodo.id.lower() == "git"
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "which":
        return bool(nodo.args) and _es_git(nodo.args[0])
    return False


def lanzamientos_de_git(codigo: str) -> list[int]:
    """Lineas donde un subprocess lanza git directamente (sin pasar por el helper)."""
    lineas = []
    for nodo in ast.walk(ast.parse(codigo)):
        if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)):
            continue
        if nodo.func.attr not in _LANZADORES or not nodo.args:
            continue
        cmd = nodo.args[0]
        if isinstance(cmd, (ast.List, ast.Tuple)) and cmd.elts and _es_git(cmd.elts[0]):
            lineas.append(nodo.lineno)
    return lineas


def archivos_de_tests() -> list[Path]:
    carpetas = [RAIZ / "tests", *RAIZ.joinpath("apps").glob("**/tests"), *RAIZ.joinpath("platform").glob("**/tests")]
    archivos = []
    for carpeta in carpetas:
        if "node_modules" in carpeta.parts:
            continue
        archivos += [p for p in carpeta.rglob("*.py") if p.name not in _EXCLUIDOS and "node_modules" not in p.parts]
    return sorted(set(archivos))


def test_ningun_test_lanza_git_sin_el_helper():
    archivos = archivos_de_tests()
    assert archivos, "el recorrido no encontro tests"
    infractores = [
        f"{p.relative_to(RAIZ).as_posix()}:{n}"
        for p in archivos
        for n in lanzamientos_de_git(p.read_text(encoding="utf-8"))
    ]
    assert not infractores, f"usar tests/git_aislado.run_git en lugar de subprocess directo: {infractores}"


def test_el_detector_marca_un_subprocess_de_git_de_ejemplo(tmp_path):
    malo = tmp_path / "test_ejemplo.py"
    malo.write_text(
        'import subprocess\n\ndef test_x():\n    subprocess.run(["git", "init"])\n'
        '    subprocess.check_output([GIT, "log"])\n',
        encoding="utf-8",
    )
    assert lanzamientos_de_git(malo.read_text(encoding="utf-8")) == [4, 5]


def test_el_detector_acepta_el_helper_y_otros_comandos():
    bueno = (
        "import subprocess\nfrom git_aislado import run_git\n"
        "def test_x():\n    run_git('init')\n    subprocess.run(['bash', 'x.sh'])\n"
    )
    assert lanzamientos_de_git(bueno) == []


def test_env_sin_git_quita_git_heredado_y_desactiva_fsmonitor(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/otro/repo")
    monkeypatch.setenv("GIT_WORK_TREE", "/otro")
    env = git_aislado.env_sin_git()
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("core.fsmonitor", "false")


@requiere_git
def test_run_git_antepone_la_opcion_sin_fsmonitor(monkeypatch):
    visto = {}

    def falso(cmd, **kw):
        visto["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(git_aislado.subprocess, "run", falso)
    git_aislado.run_git("status")
    assert visto["cmd"][1:4] == ["-c", "core.fsmonitor=false", "status"]


def _daemon_activo(repo: Path, env: dict[str, str]) -> bool:
    r = subprocess.run(  # noqa: S603 - consulta de solo lectura, no crea el daemon
        [GIT, "-C", str(repo), "fsmonitor--daemon", "status"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode == 0


@requiere_git
def test_repo_bajo_config_global_con_fsmonitor_true_no_lanza_daemon(tmp_path):
    """Mecanismo: `git fsmonitor--daemon status` sale 0 solo si hay un daemon vigilando ese
    directorio (portable a Windows/macOS/Linux; donde git no lo soporta, sale distinto de 0)."""
    global_cfg = tmp_path / "gitconfig-global"
    global_cfg.write_text("[core]\n\tfsmonitor = true\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = git_aislado.env_sin_git()
    env["GIT_CONFIG_GLOBAL"] = str(global_cfg)
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    git_aislado.run_git("init", "-q", cwd=repo, env=env)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git_aislado.run_git("add", "-A", cwd=repo, env=env)
    git_aislado.run_git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", cwd=repo, env=env)

    efectivo = git_aislado.run_git("config", "--get", "core.fsmonitor", cwd=repo, env=env).stdout.strip()
    assert efectivo == "false"
    assert not _daemon_activo(repo, env)
    assert os.path.isdir(repo / ".git")
