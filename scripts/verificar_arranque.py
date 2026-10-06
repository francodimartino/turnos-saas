"""Prueba de arranque de los servicios de `render.yaml` contra una base Supabase local.

Para cada web service: levanta su `startCommand` real (con un puerto libre en lugar de `$PORT`) con las variables
que declara el Blueprint (las `sync: false` se completan con valores de prueba locales) y espera `GET /health` = 200.
Para cada Cron Job: ejecuta su `startCommand` y exige código de salida 0 (la base local no tiene trabajo pendiente).

Requisitos: `supabase start` corriendo y las dependencias instaladas. Los procesos corren desde un directorio
temporal (nunca desde la raíz del repo), así que solo importan lo que haya instalado el intérprete elegido.

Uso:
    python scripts/verificar_arranque.py                      # intérprete actual, con el proyecto instalado
    python scripts/verificar_arranque.py --python <venv>/bin/python
    python scripts/verificar_arranque.py --desde-fuentes      # sin instalar: usa las raíces de pyproject.toml

Código de salida: 0 si todo arrancó, 1 si algo falló.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

import yaml

RAIZ = Path(__file__).resolve().parent.parent
ROLES = {
    "BOT_DATABASE_URL": "bot_service",
    "DASHBOARD_DATABASE_URL": "dashboard_service",
    "ADMIN_DATABASE_URL": "platform_admin",
    "ADMIN_READONLY_DATABASE_URL": "platform_admin_readonly",
    "PURGE_DATABASE_URL": "purge_job",
    "SCHEDULER_DATABASE_URL": "scheduler_job",
}
ESPERA_HEALTH_SEG = 60
ESPERA_JOB_SEG = 120


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raices_de_fuentes() -> list[str]:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = texto.split("[tool.pytest.ini_options]")[1].split("pythonpath = [")[1].split("]")[0]
    return [str(RAIZ / r) for r in re.findall(r'"([^"]+)"', bloque)]


async def _asignar_passwords(db_url: str, passwords: dict[str, str]) -> None:
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        for rol, password in passwords.items():
            sentencia = await conn.fetchval(
                "select format('alter role %I with login password %L', $1::text, $2::text)", rol, password
            )
            await conn.execute(sentencia)
    finally:
        await conn.close()


def _valor_de_prueba(clave: str, supabase_url: str, dsns: dict[str, str]) -> str:
    if clave in dsns:
        return dsns[clave]
    fijos = {
        "SUPABASE_URL": supabase_url,
        "SUPABASE_JWKS_URL": supabase_url + "/auth/v1/.well-known/jwks.json",
        "DASHBOARD_ORIGIN": "http://localhost:5173",
        "ADMIN_ORIGIN": "http://localhost:5174",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:5173/calendar/callback",
        "CALENDAR_OAUTH_STATE_SECRET": secrets.token_urlsafe(32),
        "SENTRY_DSN": "",  # Sentry apagado: la prueba no envía nada a ningún lado
    }
    return fijos.get(clave, "valor-de-prueba-local")


def _entorno_de(servicio: dict, supabase_url: str, dsns: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    heredadas = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    entorno = {k: v for k, v in os.environ.items() if k in heredadas}
    entorno.update(extra)
    for variable in servicio.get("envVars", []):
        clave = variable["key"]
        if variable.get("sync") is False:
            entorno[clave] = _valor_de_prueba(clave, supabase_url, dsns)
        else:
            entorno[clave] = str(variable["value"])
    entorno.pop("PYTHON_VERSION", None)
    return entorno


def _comando(servicio: dict, python: str, puerto: int | None) -> list[str]:
    partes = servicio["startCommand"].replace("$PORT", str(puerto or 0)).split()
    if partes[0] == "uvicorn":
        return [python, "-m", "uvicorn", *partes[1:]]
    if partes[0] == "python":
        return [python, *partes[1:]]
    raise ValueError(f"comando de arranque no soportado: {partes[0]}")


def _cola(archivo: Path, lineas: int = 25) -> str:
    return "\n".join(archivo.read_text(encoding="utf-8", errors="replace").splitlines()[-lineas:])


def _health(puerto: int) -> int | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/health", timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


def _probar_web(nombre: str, servicio: dict, python: str, entorno: dict[str, str], cwd: str) -> bool:
    puerto = _puerto_libre()
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "salida.log"
        with log.open("w", encoding="utf-8") as salida:
            proceso = subprocess.Popen(
                _comando(servicio, python, puerto), env=entorno, cwd=cwd, stdout=salida, stderr=subprocess.STDOUT
            )
            estado = None
            try:
                limite = time.monotonic() + ESPERA_HEALTH_SEG
                while time.monotonic() < limite and proceso.poll() is None:
                    estado = _health(puerto)
                    if estado == 200:
                        break
                    time.sleep(1)
            finally:
                if proceso.poll() is None:
                    proceso.terminate()
                    try:
                        proceso.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proceso.kill()
        ok = estado == 200
        print(f"{'OK  ' if ok else 'FALLA'} {nombre}: {servicio['startCommand']} -> GET /health = {estado}")
        if not ok:
            print(_cola(log))
        return ok


def _probar_job(nombre: str, servicio: dict, python: str, entorno: dict[str, str], cwd: str) -> bool:
    try:
        resultado = subprocess.run(
            _comando(servicio, python, None),
            env=entorno,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=ESPERA_JOB_SEG,
        )
    except subprocess.TimeoutExpired:
        print(f"FALLA {nombre}: {servicio['startCommand']} no terminó en {ESPERA_JOB_SEG}s")
        return False
    ok = resultado.returncode == 0
    print(f"{'OK  ' if ok else 'FALLA'} {nombre}: {servicio['startCommand']} -> exit {resultado.returncode}")
    if not ok:
        print((resultado.stdout + resultado.stderr)[-2000:])
    return ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--python", default=sys.executable, help="intérprete con el proyecto instalado")
    p.add_argument("--desde-fuentes", action="store_true", help="sin instalar: usa las raíces de pyproject.toml")
    p.add_argument("--db-url", default="postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    p.add_argument("--supabase-url", default="http://127.0.0.1:54321")
    args = p.parse_args(argv)

    passwords = {rol: secrets.token_hex(16) for rol in ROLES.values()}
    asyncio.run(_asignar_passwords(args.db_url, passwords))
    destino = urlsplit(args.db_url)
    dsns = {
        var: f"postgresql://{rol}:{quote(passwords[rol])}@{destino.hostname}:{destino.port}{destino.path}"
        for var, rol in ROLES.items()
    }
    extra = {}
    if args.desde_fuentes:
        previo = [os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []
        extra["PYTHONPATH"] = os.pathsep.join([*_raices_de_fuentes(), *previo])

    blueprint = yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))
    resultados = []
    with tempfile.TemporaryDirectory() as cwd:
        for servicio in blueprint["services"]:
            entorno = _entorno_de(servicio, args.supabase_url, dsns, extra)
            probar = _probar_web if servicio["type"] == "web" else _probar_job
            resultados.append(probar(servicio["name"], servicio, args.python, entorno, cwd))
    print("Todo arrancó." if all(resultados) else "Hubo fallas de arranque.")
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
