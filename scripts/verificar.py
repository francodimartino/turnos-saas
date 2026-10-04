"""Verificación local del proyecto: mismos chequeos que el CI.

Uso: python scripts/verificar.py tests | build

- tests: pytest y, si hay Supabase local levantado, `supabase test db`.
- build: typecheck y build de cada frontend con package.json (no hace nada si no hay).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTENDS = ["apps/dashboard-frontend", "apps/admin-frontend"]


def correr(cmd: list[str], cwd: Path = RAIZ) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=cwd)


def tests() -> int:
    rc = correr([sys.executable, "-m", "pytest"])
    if rc == 5:  # todavía no hay tests: no es un fallo
        rc = 0
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
        for script in ("typecheck", "build"):
            rc = correr([npm, "run", script, "--if-present"], cwd=carpeta)
            if rc != 0:
                return rc
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("tests", "build"):
        sys.exit("uso: python scripts/verificar.py tests|build")
    sys.exit(tests() if sys.argv[1] == "tests" else build())
