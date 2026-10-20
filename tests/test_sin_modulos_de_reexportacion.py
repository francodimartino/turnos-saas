"""Ningún módulo reexporta nombres de otro: quien los necesita los importa de donde viven.

Un módulo de reexportación puro es un archivo que solo tiene docstring, imports y `__all__`. Solo se admite
como `__init__.py` de un paquete, donde define la interfaz pública.
"""

import ast
from pathlib import Path

from git_aislado import run_git

RAIZ = Path(__file__).resolve().parent.parent


def es_reexportacion_pura(codigo: str) -> bool:
    """True si el módulo solo contiene docstring, imports y la asignación de `__all__` (con al menos un import)."""
    cuerpo = ast.parse(codigo).body
    hay_import = False
    for nodo in cuerpo:
        if isinstance(nodo, ast.Import | ast.ImportFrom):
            hay_import = True
        elif isinstance(nodo, ast.Expr) and isinstance(nodo.value, ast.Constant) and isinstance(nodo.value.value, str):
            continue
        elif isinstance(nodo, ast.Assign | ast.AnnAssign | ast.AugAssign):
            destinos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            if not all(isinstance(d, ast.Name) and d.id == "__all__" for d in destinos):
                return False
        else:
            return False
    return hay_import


def _modulos_versionados():
    salida = run_git("ls-files", "-z", "*.py", cwd=RAIZ).stdout
    return [RAIZ / f for f in salida.split("\0") if f.endswith(".py") and (RAIZ / f).is_file()]


def test_detecta_un_modulo_de_reexportacion_pura():
    assert es_reexportacion_pura('"""Doc."""\n\nfrom otro import A, B\n\n__all__ = ["A", "B"]\n')
    assert es_reexportacion_pura("import otro\n__all__ = []\n")


def test_no_confunde_un_modulo_con_logica():
    assert not es_reexportacion_pura("from otro import A\n\n\ndef f():\n    return A\n")
    assert not es_reexportacion_pura("from otro import A\n\nX = A\n")
    assert not es_reexportacion_pura('"""Solo doc."""\n')


def test_solo_los_init_de_paquete_reexportan():
    culpables = [
        p.relative_to(RAIZ).as_posix()
        for p in _modulos_versionados()
        if p.name != "__init__.py" and es_reexportacion_pura(p.read_text(encoding="utf-8"))
    ]
    assert not culpables, f"Módulos de reexportación pura fuera de un __init__.py: {culpables}"
