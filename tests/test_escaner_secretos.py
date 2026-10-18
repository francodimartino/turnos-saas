"""Escaner de secretos (`.github/scripts/escanear-secretos.sh`): patrones del propio stack.

Los valores sospechosos se arman en tiempo de ejecucion para no dejar secretos literales
en el repo. Para no lanzar un proceso bash/git por caso (se agotan bajo carga: la suite
completa corre en paralelo con otras), los casos se agrupan: un repo temporal por modulo, un
commit con un archivo por caso y UN escaneo por grupo; cada caso afirma sobre su linea de la
salida. Todo subprocess lleva timeout explicito.
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

# Tope por subproceso: holgado para una maquina cargada, pero finito (un cuelgue falla, no traba).
TIMEOUT = 120


# Raiz permitida para los repos temporales; la fija el fixture `_raiz_temporal`.
_RAIZ_TEMPORAL: list[Path] = []


@pytest.fixture(autouse=True, scope="module")
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
        timeout=TIMEOUT,
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
        timeout=TIMEOUT,
    )


def _repo_base(tmp_path_factory, nombre: str) -> Path:
    repo = tmp_path_factory.mktemp(nombre)
    _git(repo, "init", "-q")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _escanear_casos(repo: Path, casos: dict[str, str]) -> subprocess.CompletedProcess:
    """Un commit con un archivo `caso_<n>.txt` por caso y un unico escaneo del rango."""
    base = _git(repo, "rev-parse", "HEAD")
    for i, linea in enumerate(casos.values()):
        (repo / f"caso_{i}.txt").write_text(linea + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "casos")
    return _escanear(repo, f"{base}...HEAD")


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


NOMBRES = {
    "JWT": "JWT (clave de Supabase)",
    "Telegram": "token de bot de Telegram",
    "Postgres": "URL de Postgres con credenciales",
    "GOCSPX": "client secret de Google (GOCSPX)",
    "APP_USR": "credencial de Mercado Pago (APP_USR)",
    "Sentry": "DSN de Sentry",
    "sb_secret": "secret key de Supabase (sb_secret)",
    "sbp": "access token de Supabase (sbp)",
}

# Claves con forma de clave real aunque contengan [<{$] pasada la zona de la clave.
POSTGRES_POSITIVOS = {
    "Postgres con $ y llaves dentro de la clave": "postgres" + "://u:" + "ab$cd9{x}" + "@db.ejemplo.com",
}


@pytest.fixture(scope="module")
def positivos(tmp_path_factory):
    repo = _repo_base(tmp_path_factory, "positivos")
    casos = {c: f"CLAVE={v}" for c, v in _valores().items()}
    casos.update({c: f"CLAVE={v}" for c, v in POSTGRES_POSITIVOS.items()})
    r = _escanear_casos(repo, casos)
    archivos = {c: f"caso_{i}.txt" for i, c in enumerate(casos)}
    return r, archivos


@pytest.mark.parametrize("clave", [*_valores(), *POSTGRES_POSITIVOS])
def test_detecta_cada_patron_sin_revelar_el_valor(positivos, clave):
    r, archivos = positivos
    valores = {**_valores(), **POSTGRES_POSITIVOS}
    nombre = NOMBRES.get(clave, "URL de Postgres con credenciales")
    assert r.returncode == 1, r.stdout + r.stderr
    salida = r.stdout + r.stderr
    esperado = f"{nombre} en {archivos[clave]}"
    assert esperado in salida, salida
    assert valores[clave] not in salida


NEGATIVOS = [
    "DB=postgresql://postgres:postgres@127.0.0.1:54322/postgres",
    "DB=postgresql://postgres:postgres@localhost:5432/postgres",
    "TOKEN=123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "DB=postgresql://bot_service:x@h:5432/db",
    # clave que es una plantilla (la exclusion aplica solo a la zona de la clave)
    "DB=postgres://u:${PASS}@db.ejemplo.com/app",
    "DB=postgres://u:<clave>@db.ejemplo.com/app",
    "DB=postgres://u:{{clave}}@db.ejemplo.com/app",
    "DB=postgres://u:${PASS}@h",
    "DB=postgres://u:<clave>@h",
    "DB=postgres://u:{{clave}}@h",
    # claves demo de Supabase local (anon / service_role), armadas en tiempo de ejecucion
    "KEY="
    + "ey"
    + "J"
    + "hbGciOiJIUzI1NiJ9.ey"
    + "J"
    + "pc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24ifQ.firmaDemo1234567",
    "KEY="
    + "ey"
    + "J"
    + "hbGciOiJIUzI1NiJ9.ey"
    + "J"
    + "pc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSJ9.firmaDemo1234567",
]


@pytest.fixture(scope="module")
def negativos(tmp_path_factory):
    repo = _repo_base(tmp_path_factory, "negativos")
    # un escaneo por caso seria un proceso por caso: se escanean todos juntos y, si algo se
    # marca, la salida nombra el archivo del caso (`caso_<n>.txt`)
    return _escanear_casos(repo, {str(i): linea for i, linea in enumerate(NEGATIVOS)})


@pytest.mark.parametrize("indice", range(len(NEGATIVOS)))
def test_no_marca_valores_locales_conocidos(negativos, indice):
    salida = negativos.stdout + negativos.stderr
    assert f"caso_{indice}.txt" not in salida, salida
    assert negativos.returncode == 0, salida


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
        timeout=TIMEOUT,
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
    for args in (
        ["init", "-q"],
        ["-c", "user.email=a@a", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "ajeno"],
    ):
        subprocess.run([GIT, "-C", str(ajeno), *args], env=env0, check=True, capture_output=True, timeout=TIMEOUT)
    antes_log = subprocess.run(
        [GIT, "-C", str(ajeno), "log", "--oneline"], env=env0, capture_output=True, text=True, timeout=TIMEOUT
    ).stdout
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

    assert (
        subprocess.run(
            [GIT, "-C", str(ajeno), "log", "--oneline"], env=env0, capture_output=True, text=True, timeout=TIMEOUT
        ).stdout
        == antes_log
    )
    assert (ajeno / ".git" / "config").read_text(encoding="utf-8") == antes_cfg
    assert not (ajeno / "x.txt").exists()


def test_la_guardia_rechaza_repos_fuera_del_temporal():
    with pytest.raises(AssertionError):
        _guardia(RAIZ)
