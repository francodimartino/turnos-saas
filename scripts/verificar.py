"""Verificación local del proyecto: mismos chequeos que el CI.

Uso: python scripts/verificar.py tests | build

- tests: `ruff check .` (el lint del CI), pytest, `npm test` de cada frontend con
  package.json y, si hay Supabase local levantado, `supabase test db`.
- build: typecheck y build de cada frontend con package.json (no hace nada si no hay).

Antes de correr npm en un frontend se instalan sus dependencias con `npm ci` si falta
node_modules o si package-lock.json es más nuevo que lo instalado.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTENDS = ["apps/dashboard-frontend", "apps/admin-frontend", "platform/admin-frontend-template"]


def correr(cmd: list[str], cwd: Path = RAIZ) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=cwd)


def frontends_con_paquete() -> list[Path]:
    return [RAIZ / a for a in FRONTENDS if (RAIZ / a / "package.json").exists()]


def instalar_dependencias(carpeta: Path, npm: str) -> int:
    """`npm ci` si falta node_modules o el lockfile cambió desde la última instalación."""
    instalado = carpeta / "node_modules" / ".package-lock.json"
    lock = carpeta / "package-lock.json"
    if instalado.exists() and (not lock.exists() or instalado.stat().st_mtime >= lock.stat().st_mtime):
        return 0
    return correr([npm, "ci" if lock.exists() else "install"], cwd=carpeta)


def tests_frontends() -> int:
    carpetas = frontends_con_paquete()
    if not carpetas:
        print("Frontends: sin package.json todavía.")
        return 0
    npm = shutil.which("npm")
    if not npm:
        print("npm no está disponible")
        return 1
    for carpeta in carpetas:
        rc = instalar_dependencias(carpeta, npm) or correr([npm, "test", "--if-present"], cwd=carpeta)
        if rc != 0:
            return rc
    return 0


def lint() -> int:
    """`ruff check .` con la configuración de pyproject.toml, igual que el CI. No se saltea."""
    if subprocess.run([sys.executable, "-m", "ruff", "--version"], cwd=RAIZ, capture_output=True).returncode != 0:
        print("ruff no está instalado: instalalo con `python -m pip install ruff` "
              "(o `pip install -e .[dev]`; está en las dependencias de desarrollo de pyproject.toml).")
        return 1
    return correr([sys.executable, "-m", "ruff", "check", "."])


def tests() -> int:
    rc = lint()
    if rc != 0:
        return rc

    rc = correr([sys.executable, "-m", "pytest"])
    if rc == 5:  # todavía no hay tests: no es un fallo
        rc = 0
    if rc != 0:
        return rc

    rc = tests_frontends()
    if rc != 0:
        return rc

    # La CLI de Supabase solo ve supabase/: los tests de platform/infra se copian a supabase/tests.
    for f in (RAIZ / "platform/infra/pgtap-tests").glob("*.sql"):
        shutil.copy(f, RAIZ / "supabase/tests" / f"platform_{f.name}")
    if not any((RAIZ / "supabase/tests").glob("*.sql")):
        print("pgTAP: no hay tests .sql todavía.")
        return 0
    supabase = shutil.which("supabase")
    if not supabase:
        print("pgTAP OMITIDO: falta la CLI de Supabase (se corre en el CI).")
        return 0
    estado = subprocess.run([supabase, "status"], cwd=RAIZ, capture_output=True)
    if estado.returncode != 0:
        print("pgTAP OMITIDO: Supabase local no está levantado (`supabase start`); se corre en el CI.")
        return 0
    return correr([supabase, "test", "db"])


def build() -> int:
    npm = shutil.which("npm")
    for app in FRONTENDS:
        carpeta = RAIZ / app
        if not (carpeta / "package.json").exists():
            print(f"{app}: sin package.json todavía.")
            continue
        if not npm:
            print("npm no está disponible")
            return 1
        rc = instalar_dependencias(carpeta, npm)
        if rc != 0:
            return rc
        for script in ("typecheck", "build"):
            rc = correr([npm, "run", script, "--if-present"], cwd=carpeta)
            if rc != 0:
                return rc
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("tests", "build"):
        sys.exit("uso: python scripts/verificar.py tests|build")
    sys.exit(tests() if sys.argv[1] == "tests" else build())
