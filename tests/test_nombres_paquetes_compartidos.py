"""Los paquetes compartidos llevan el prefijo `turnos_`; los nombres anteriores no vuelven a aparecer."""

import re
from pathlib import Path

from git_aislado import run_git

RAIZ = Path(__file__).resolve().parent.parent
RETIRADOS = ("observabilidad", "proteccion_login", "seguridad_http", "supabase_proxy", "telefonos")
# Referencias a un módulo Python con el nombre viejo: import, ruta de carpeta o nombre de logger.
_N = "|".join(RETIRADOS)
PATRON = re.compile(
    rf"(^\s*(?:from|import)\s+(?:{_N}))|(apps/shared/(?:{_N}))|([\"'](?:{_N})\.\w)",
    re.MULTILINE,
)
ESTE = Path(__file__).name


def _versionados():
    salida = run_git("ls-files", "-z", cwd=RAIZ).stdout
    return [RAIZ / f for f in salida.split("\0") if f.endswith((".py", ".md", ".yml", ".yaml", ".toml"))]


def test_las_carpetas_compartidas_tienen_prefijo_turnos():
    nombres = {d.name for d in (RAIZ / "apps" / "shared").iterdir() if (d / "__init__.py").is_file()}
    assert nombres and all(n.startswith("turnos_") for n in nombres), nombres
    assert not nombres & set(RETIRADOS)


def test_ninguna_referencia_a_los_nombres_retirados():
    hallazgos = []
    for archivo in _versionados():
        if archivo.name == ESTE or not archivo.is_file():
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        if PATRON.search(texto):
            hallazgos.append(str(archivo.relative_to(RAIZ)))
    assert not hallazgos, hallazgos
