"""Regenera `platform/admin-frontend-template` a partir de `apps/admin-frontend`, que es la única fuente.

    python scripts/sincronizar_admin_frontend.py             # reescribe la plantilla
    python scripts/sincronizar_admin_frontend.py --verificar # solo compara; sale con 1 si difiere

La plantilla es una copia exacta de la fuente, salvo `node_modules`, `dist` y lo que declara `EXCLUSIONES`.
Un test de pytest (`tests/test_admin_frontend_sincronizado.py`) corre siempre en el CI y falla si la plantilla
no es lo que produciría este script. Los cambios se hacen siempre en `apps/admin-frontend`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = Path("apps/admin-frontend")
PLANTILLA = Path("platform/admin-frontend-template")
COMANDO = "python scripts/sincronizar_admin_frontend.py"

# Carpetas que no se copian (generadas o instaladas).
CARPETAS_IGNORADAS = frozenset({"node_modules", "dist", "dist-ssr", "coverage"})
# Rutas relativas (con `/`) de la fuente que son propias de la fuente y no van a la plantilla, cada una con su
# motivo. Hoy no hay ninguna: README.md y vercel.json son iguales en las dos carpetas.
EXCLUSIONES: dict[str, str] = {}


def _ignorado(relativa: Path) -> bool:
    return (
        any(parte in CARPETAS_IGNORADAS for parte in relativa.parts)
        or relativa.name.endswith(".tsbuildinfo")
        or relativa.as_posix() in EXCLUSIONES
    )


def listar(carpeta: Path) -> dict[str, Path]:
    """Archivos de `carpeta` que cuentan para la comparación: ruta relativa (con `/`) -> ruta real."""
    if not carpeta.is_dir():
        return {}
    salida = {}
    for p in sorted(carpeta.rglob("*")):
        if p.is_file() and not _ignorado(p.relative_to(carpeta)):
            salida[p.relative_to(carpeta).as_posix()] = p
    return salida


def diferencias(fuente: Path, plantilla: Path) -> list[str]:
    """Qué hace falta para que `plantilla` sea lo que produciría la sincronización (vacío = sincronizada)."""
    origen, destino = listar(fuente), listar(plantilla)
    problemas = [f"falta en la plantilla: {r}" for r in sorted(origen.keys() - destino.keys())]
    problemas += [f"sobra en la plantilla: {r}" for r in sorted(destino.keys() - origen.keys())]
    for r in sorted(origen.keys() & destino.keys()):
        if origen[r].read_bytes() != destino[r].read_bytes():
            problemas.append(f"distinto: {r}")
    return problemas


def sincronizar(fuente: Path, plantilla: Path) -> None:
    origen = listar(fuente)
    for r, p in listar(plantilla).items():
        if r not in origen:
            p.unlink()
    for r, p in origen.items():
        destino = plantilla / r
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, destino)
    # Carpetas que quedaron vacías (sin contar las ignoradas, como node_modules).
    for d in sorted((p for p in plantilla.rglob("*") if p.is_dir()), reverse=True):
        if not _ignorado(d.relative_to(plantilla)) and not any(d.iterdir()):
            d.rmdir()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sincroniza la plantilla del admin-frontend desde la fuente.")
    ap.add_argument("--verificar", action="store_true", help="no escribe: sale con 1 si la plantilla difiere")
    args = ap.parse_args(argv)
    fuente, plantilla = RAIZ / FUENTE, RAIZ / PLANTILLA
    if args.verificar:
        problemas = diferencias(fuente, plantilla)
        for p in problemas:
            print(p)
        if problemas:
            print(f"La plantilla no coincide con la fuente. Resincronizá con: {COMANDO}")
            return 1
        print("La plantilla coincide con la fuente.")
        return 0
    sincronizar(fuente, plantilla)
    print(f"{PLANTILLA.as_posix()} sincronizada desde {FUENTE.as_posix()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
