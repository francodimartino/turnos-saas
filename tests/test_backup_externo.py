"""Backup externo: el workflow (`.github/workflows/backup.yml`) y el script que usa (`backup-base.sh`).

El workflow se parsea y se verifican sus condiciones de seguridad. El script se ejecuta con
`pg_dump`, `age` y `aws` falsos en el PATH: el "destino" es una carpeta local, nunca un servicio real.
"""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOW = RAIZ / ".github" / "workflows" / "backup.yml"
SCRIPT = RAIZ / ".github" / "scripts" / "backup-base.sh"
BASH = shutil.which("bash")
TIMEOUT = 120

SECRETS_ESPERADOS = {
    "BACKUP_DATABASE_URL",
    "BACKUP_AGE_PUBLIC_KEY",
    "BACKUP_BUCKET",
    "BACKUP_ENDPOINT",
    "BACKUP_ACCESS_KEY_ID",
    "BACKUP_SECRET_ACCESS_KEY",
}


def _cargar():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _pasos():
    return _cargar()["jobs"]["backup"]["steps"]


def test_dispara_diario_y_manual():
    disparadores = _cargar()[True]  # PyYAML lee la clave `on` como booleano
    assert re.fullmatch(r"\d+ \d+ \* \* \*", disparadores["schedule"][0]["cron"])
    assert "workflow_dispatch" in disparadores


def test_permisos_minimos():
    assert _cargar()["permissions"] == {"contents": "read"}


def test_acciones_fijadas_por_sha():
    usos = [p["uses"] for p in _pasos() if "uses" in p]
    assert usos
    for uso in usos:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uso), uso


def test_instala_el_cliente_de_la_version_de_postgres_del_proyecto():
    config = (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8")
    mayor = re.search(r"^major_version\s*=\s*(\d+)", config, re.MULTILINE).group(1)
    instalar = "\n".join(p.get("run", "") for p in _pasos())
    assert f"postgresql-client-{mayor}" in instalar
    assert re.search(r"\bage\b", instalar)


def test_los_valores_sensibles_salen_de_secrets():
    env = {}
    for paso in _pasos():
        env.update(paso.get("env", {}))
    secretos = {re.fullmatch(r"\$\{\{ secrets\.(\w+) \}\}", v).group(1) for v in env.values() if "secrets." in str(v)}
    assert secretos == SECRETS_ESPERADOS
    literales = {k: v for k, v in env.items() if "secrets." not in str(v)}
    assert literales == {"AWS_DEFAULT_REGION": "auto"}


def test_el_workflow_no_imprime_ni_sube_artefactos():
    texto = WORKFLOW.read_text(encoding="utf-8")
    assert "upload-artifact" not in texto
    assert not re.search(r"\becho\b.*secrets\.", texto)
    assert "set -x" not in texto
    assert "backup-base.sh" in texto


def test_el_script_cifra_antes_de_salir_y_excluye_vault():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "--exclude-schema=vault" in script
    assert "--no-owner" in script and "--no-privileges" in script
    assert re.search(r"pg_dump [^\n]*\\\n\s*\| age ", script)  # el dump va por pipe a age, nunca a disco
    assert "set -x" not in script
    # Lo unico que se sube es el archivo cifrado (`.age`).
    assert 'archivo="$TMP/base-$sello.dump.age"' in script
    assert 'aws s3 cp "$archivo"' in script


# --- Ejecucion del script con herramientas falsas ---------------------------------------------------

requiere_bash = pytest.mark.skipif(BASH is None, reason="requiere bash")

_PG_DUMP = """#!/usr/bin/env bash
echo "$@" > "$FAKE_LOG/pg_dump.args"
printf 'CONTENIDO-SIN-CIFRAR'
"""
_AGE = """#!/usr/bin/env bash
echo "$@" > "$FAKE_LOG/age.args"
while [ $# -gt 0 ]; do [ "$1" = "--output" ] && salida="$2"; shift; done
{ printf 'age-encryption.org/v1\\n'; cat | tr 'A-Z' 'a-z'; } > "$salida"
"""
_AWS = """#!/usr/bin/env bash
echo "$@" > "$FAKE_LOG/aws.args"
mkdir -p "$FAKE_LOG/bucket" && cp "$3" "$FAKE_LOG/bucket/subido.age"
"""


def _entorno(tmp_path: Path, **extra) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for nombre, cuerpo in (("pg_dump", _PG_DUMP), ("age", _AGE), ("aws", _AWS)):
        f = bin_dir / nombre
        f.write_bytes(cuerpo.encode())
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": log.as_posix(),
        "BACKUP_DATABASE_URL": "postgresql://usuario:clave-falsa@127.0.0.1:1/base",
        "BACKUP_AGE_PUBLIC_KEY": "age1clavepublicafalsa",
        "BACKUP_BUCKET": "bucket-falso",
        "BACKUP_ENDPOINT": "http://127.0.0.1:1",
        "AWS_ACCESS_KEY_ID": "id-falso",
        "AWS_SECRET_ACCESS_KEY": "valor-falso",
        "BACKUP_TMP": (tmp_path / "tmp").as_posix(),
    }
    env.update(extra)
    return env


def _correr(env: dict):
    return subprocess.run(  # noqa: S603 - bash y script del propio repo
        [BASH, SCRIPT.as_posix()], env=env, capture_output=True, text=True, timeout=TIMEOUT
    )


@requiere_bash
def test_script_sube_solo_el_archivo_cifrado(tmp_path):
    r = _correr(_entorno(tmp_path))
    assert r.returncode == 0, r.stderr
    log = tmp_path / "log"
    pg = (log / "pg_dump.args").read_text()
    assert "--exclude-schema=vault" in pg and "--no-owner" in pg and "--no-privileges" in pg
    assert "--recipient age1clavepublicafalsa" in (log / "age.args").read_text()
    aws = (log / "aws.args").read_text()
    assert aws.startswith("s3 cp ") and "s3://bucket-falso/turnos-saas/" in aws and ".dump.age" in aws
    assert "--endpoint-url http://127.0.0.1:1" in aws
    subido = (log / "bucket" / "subido.age").read_bytes()
    assert subido.startswith(b"age-encryption.org/v1")
    assert b"CONTENIDO-SIN-CIFRAR" not in subido
    salida = r.stdout + r.stderr
    assert "valor-falso" not in salida and "clave-falsa" not in salida
    assert not (tmp_path / "tmp").exists()  # el temporal se borra


@requiere_bash
def test_script_falla_sin_variables_y_no_sube(tmp_path):
    env = _entorno(tmp_path)
    del env["BACKUP_BUCKET"]
    r = _correr(env)
    assert r.returncode != 0 and "BACKUP_BUCKET" in r.stderr
    assert not (tmp_path / "log" / "aws.args").exists()


@requiere_bash
def test_script_rechaza_una_clave_privada(tmp_path):
    r = _correr(_entorno(tmp_path, BACKUP_AGE_PUBLIC_KEY="AGE-SECRET-KEY-X"))
    assert r.returncode != 0
    assert not (tmp_path / "log" / "pg_dump.args").exists()


@requiere_bash
def test_script_no_sube_si_pg_dump_falla(tmp_path):
    env = _entorno(tmp_path)
    (tmp_path / "bin" / "pg_dump").write_bytes(b"#!/usr/bin/env bash\nexit 3\n")
    r = _correr(env)
    assert r.returncode != 0
    assert not (tmp_path / "log" / "aws.args").exists()
