"""Quién puede importar a quién: ruff (TID251) lo hace cumplir y este test prueba cada fila con un caso negativo.

Cada carpeta con reglas tiene su `ruff.toml` (hereda `pyproject.toml`). Acá se le pasa a ruff por stdin un archivo
con un import prohibido, ubicado en esa carpeta, y se comprueba que lo marca; y uno permitido, que no lo marca.
No se escribe nada en disco, así que no puede dejar archivos sueltos en el repo.
"""

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

APPS = ["dashboard_backend", "admin_backend", "turnos_bot", "jobs_comun", "jobs_purga", "jobs_vencimiento"]
COMPARTIDOS = ["turnos_config", "turnos_dominio", "turnos_http", "turnos_negocio", "turnos_sesion", "turnos_telefonos"]
WEB = ["fastapi", "starlette"]

# (carpeta del archivo simulado, módulo prohibido)
PROHIBIDOS = (
    # platform_* no importa apps/ ni el dominio de turnos (ni lo compartido de turnos_*).
    [("platform/packages/platform_auth/platform_auth", m) for m in APPS + COMPARTIDOS]
    # platform_notifications tampoco depende de un framework web.
    + [("platform/packages/platform_notifications/platform_notifications", m) for m in APPS + COMPARTIDOS + WEB]
    # Los paquetes compartidos no importan apps; el dominio, tampoco canales ni framework web.
    + [("apps/shared/turnos_sesion", m) for m in APPS]
    + [("apps/shared/turnos_dominio", m) for m in APPS + WEB]
    # Las apps no se importan entre sí ni importan jobs.
    + [("apps/bot/turnos_bot", m) for m in ["dashboard_backend", "admin_backend", "jobs_comun", "jobs_purga"]]
    + [("apps/dashboard-backend/dashboard_backend", m) for m in ["turnos_bot", "admin_backend", "jobs_purga"]]
    + [("apps/admin-backend/admin_backend", m) for m in ["turnos_bot", "dashboard_backend", "jobs_vencimiento"]]
    # Los jobs no importan apps ni framework web; jobs_comun no importa jobs; un job no importa a otro.
    + [("apps/jobs/jobs_comun", m) for m in ["dashboard_backend", "admin_backend", "turnos_bot", "jobs_purga", *WEB]]
    + [("apps/jobs/jobs_purga", m) for m in ["jobs_vencimiento", "dashboard_backend", "turnos_bot", *WEB]]
    + [("apps/jobs/jobs_vencimiento", m) for m in ["jobs_purga", "admin_backend", "turnos_bot", *WEB]]
)

# (carpeta, módulo) que sí se puede importar.
PERMITIDOS = [
    ("apps/bot/turnos_bot", "turnos_dominio"),
    ("apps/dashboard-backend/dashboard_backend", "turnos_sesion"),
    ("apps/admin-backend/admin_backend", "platform_auth"),
    ("apps/jobs/jobs_purga", "jobs_comun"),
    ("apps/jobs/jobs_vencimiento", "platform_notifications"),
    ("apps/shared/turnos_sesion", "fastapi"),
    ("platform/packages/platform_auth/platform_auth", "fastapi"),
    ("tests", "dashboard_backend"),
    ("scripts", "admin_backend"),
]


def _ruff(carpeta: str, codigo: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "TID251",
            "--no-cache",
            "--stdin-filename",
            f"{carpeta}/_prueba_import_prohibido.py",
            "-",
        ],
        cwd=RAIZ,
        input=codigo,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("carpeta", "modulo"), PROHIBIDOS)
def test_ruff_marca_el_import_prohibido(carpeta, modulo):
    for codigo in (f"import {modulo}\n", f"from {modulo}.sub import x\n", f"def f():\n    import {modulo}\n"):
        r = _ruff(carpeta, codigo)
        assert r.returncode == 1, f"ruff no marcó {codigo!r} en {carpeta}: {r.stdout}{r.stderr}"
        assert "TID251" in r.stdout


@pytest.mark.parametrize(("carpeta", "modulo"), PERMITIDOS)
def test_ruff_deja_pasar_el_import_permitido(carpeta, modulo):
    r = _ruff(carpeta, f"import {modulo}\n")
    assert r.returncode == 0, f"ruff marcó un import permitido en {carpeta}: {r.stdout}{r.stderr}"


def test_cada_ruff_toml_tiene_su_fila_en_la_matriz():
    """Un `ruff.toml` nuevo sin caso negativo acá es una regla que nadie prueba."""
    carpetas = {p.parent.relative_to(RAIZ).as_posix() for p in RAIZ.rglob("ruff.toml") if "node_modules" not in p.parts}
    cubiertas = {c for c, _ in PROHIBIDOS}
    sin_probar = {c for c in carpetas if not any(x == c or x.startswith(c + "/") for x in cubiertas)}
    assert sin_probar == set()
