"""Escaner de secretos (`.github/scripts/escanear-secretos.sh`): patrones del propio stack.

Los valores sospechosos se arman en tiempo de ejecucion para no dejar secretos literales
en el repo. Cada caso corre el script sobre un rango git de un repo temporal.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / ".github" / "scripts" / "escanear-secretos.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not (BASH and GIT), reason="requiere bash y git")


# Raiz permitida para los repos temporales; la fija el fixture `_raiz_temporal`.
_RAIZ_TEMPORAL: list[Path] = []


@pytest.fixture(autouse=True)
def _raiz_temporal(tmp_path_factory):
    _RAIZ_TEMPORAL[:] = [tmp_path_factory.getbasetemp().resolve()]
    yield
    _RAIZ_TEMPORAL.clear()


def _guardia(repo: Path) -> Path:
    """Falla si el repo sobre el que se opera no esta dentro del temporal de pytest."""
    repo = repo.resolve()
    assert _RAIZ_TEMPORAL, "falta la raiz temporal"
    assert repo.is_relative_to(_RAIZ_TEMPORAL[0]), f"el test intenta operar fuera del temporal: {repo}"
    assert repo.resolve() != RAIZ.resolve()
    return repo


def _entorno(repo: Path) -> dict[str, str]:
    """Entorno aislado: sin GIT_* heredado (bajo el pre-push apuntarian al repo real) y con
    GIT_DIR/GIT_WORK_TREE explicitos al temporal."""
    repo = _guardia(repo)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_DIR"] = str(repo / ".git")
    env["GIT_WORK_TREE"] = str(repo)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(cwd: Path, *args: str) -> str:
    cwd = _guardia(cwd)
    r = subprocess.run(
        [GIT, "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        env=_entorno(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _escanear(repo: Path, rango: str) -> subprocess.CompletedProcess:
    # ruta relativa al cwd del repo temporal: bash de Git for Windows no entiende "C:\..." sin escapar
    return subprocess.run(
        [BASH, SCRIPT.as_posix(), rango],
        cwd=_guardia(repo),
        env=_entorno(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _agregar(repo: Path, nombre: str, contenido: str) -> None:
    (repo / nombre).write_text(contenido + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cambio")


def _valores() -> dict[str, str]:
    a = "A" * 12
    h32 = "0123456789abcdef" * 2
    return {
        "JWT": "ey" + "J" + "hbGciOiJIUzI1NiJ9" + ".ey" + "J" + "yb2xlIjoiYW5vbiJ9" + "." + "c2lnbmF0dXJhMTIzNDU2",
        "Telegram": "8" + "7654321" + ":" + "AAH" + "x" * 32,
        "Postgres": "postgres" + "ql://usr:" + "pw" + "9z8y7" + "@db.ejemplo-prod.supabase.co:5432/postgres",
        "GOCSPX": "GOC" + "SPX-" + a + "abcdef",
        "APP_USR": "APP" + "_USR-" + "1234567890" + "-" + a,
        "Sentry": "https://" + h32 + "@o123.ingest.sentry.io/456",
        "sb_secret": "sb" + "_secret_" + a + "abcdef",
        "sbp": "sb" + "p_" + h32 + "01234567",
    }


@pytest.mark.parametrize("clave", list(_valores()))
def test_detecta_cada_patron_sin_revelar_el_valor(repo, clave):
    valor = _valores()[clave]
    base = _git(repo, "rev-parse", "HEAD")
    _agregar(repo, "config_x.txt", f"CLAVE={valor}")
    r = _escanear(repo, f"{base}...HEAD")
    assert r.returncode == 1, r.stdout + r.stderr
    salida = r.stdout + r.stderr
    assert "config_x.txt" in salida
    assert valor not in salida


@pytest.mark.parametrize(
    "linea",
    [
        "DB=postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "DB=postgresql://postgres:postgres@localhost:5432/postgres",
        "TOKEN=123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        "DB=postgresql://bot_service:x@h:5432/db",
        # claves demo de Supabase local (anon / service_role), armadas en tiempo de ejecucion
        "KEY=" + "ey" + "J" + "hbGciOiJIUzI1NiJ9.ey" + "J" + "pc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24ifQ.firmaDemo1234567",
        "KEY=" + "ey" + "J" + "hbGciOiJIUzI1NiJ9.ey" + "J" + "pc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSJ9.firmaDemo1234567",
    ],
)
def test_no_marca_valores_locales_conocidos(repo, linea):
    base = _git(repo, "rev-parse", "HEAD")
    _agregar(repo, "ejemplo.txt", linea)
    r = _escanear(repo, f"{base}...HEAD")
    assert r.returncode == 0, r.stdout + r.stderr


def test_el_contenido_actual_del_repo_pasa_limpio(tmp_path):
    """Un rango que agrega todo el arbol versionado en un repo temporal sale con codigo 0."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "vacio")
    base = _git(tmp_path, "rev-parse", "HEAD")
    archivos = subprocess.run(
        [GIT, "-C", str(RAIZ), "ls-files", "-z"],
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for rel in filter(None, (a.decode() for a in archivos)):
        origen = RAIZ / rel
        if not origen.is_file():
            continue
        destino = tmp_path / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, destino)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "arbol")
    r = _escanear(tmp_path, f"{base}...HEAD")
    assert r.returncode == 0, r.stdout + r.stderr


def test_bajo_entorno_de_pre_push_no_toca_otro_repo(tmp_path, monkeypatch):
    """Simula el pre-push: GIT_DIR/GIT_WORK_TREE heredados apuntan a OTRO repo de prueba; ese
    repo no debe recibir commits ni cambios de config."""
    ajeno = tmp_path / "ajeno"
    ajeno.mkdir()
    env0 = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (["init", "-q"], ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "ajeno"]):
        subprocess.run([GIT, "-C", str(ajeno), *args], env=env0, check=True, capture_output=True)
    antes_log = subprocess.run([GIT, "-C", str(ajeno), "log", "--oneline"], env=env0, capture_output=True, text=True).stdout
    antes_cfg = (ajeno / ".git" / "config").read_text(encoding="utf-8")

    monkeypatch.setenv("GIT_DIR", str(ajeno / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(ajeno))
    monkeypatch.setenv("GIT_INDEX_FILE", str(ajeno / ".git" / "index"))

    propio = tmp_path / "propio"
    propio.mkdir()
    _git(propio, "init", "-q")
    (propio / "x.txt").write_text("x\n", encoding="utf-8")
    _git(propio, "add", "-A")
    _git(propio, "commit", "-qm", "propio")
    assert _git(propio, "log", "--format=%s") == "propio"

    assert subprocess.run([GIT, "-C", str(ajeno), "log", "--oneline"], env=env0, capture_output=True, text=True).stdout == antes_log
    assert (ajeno / ".git" / "config").read_text(encoding="utf-8") == antes_cfg
    assert not (ajeno / "x.txt").exists()


def test_la_guardia_rechaza_repos_fuera_del_temporal():
    with pytest.raises(AssertionError):
        _guardia(RAIZ)
