"""platform_calendar es genérico: ni sus nombres ni sus textos mencionan el negocio de las apps."""

import ast
import re
from pathlib import Path

CARPETA = Path(__file__).resolve().parent.parent
PROHIBIDAS = re.compile(r"servicio|cliente|tel[eé]fono|booking|turno", re.IGNORECASE)
# `ServicioCalendario` es la interfaz del propio paquete, no el concepto de negocio.
PERMITIDO = re.compile(r"ServicioCalendario")


def _nombres_y_textos(arbol: ast.AST):
    docstrings = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            cuerpo = nodo.body
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant):
                docstrings.add(id(cuerpo[0].value))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield nodo.name
        elif isinstance(nodo, ast.Name):
            yield nodo.id
        elif isinstance(nodo, ast.Attribute):
            yield nodo.attr
        elif isinstance(nodo, (ast.arg, ast.keyword)) and nodo.arg:
            yield nodo.arg
        elif isinstance(nodo, ast.alias):
            yield nodo.name
        elif isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and id(nodo) not in docstrings:
            yield nodo.value


def test_el_codigo_de_platform_calendar_no_menciona_servicio_cliente_ni_telefono():
    hallazgos = []
    for ruta in sorted(CARPETA.glob("*.py")):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for texto in _nombres_y_textos(arbol):
            if PROHIBIDAS.search(PERMITIDO.sub("", texto)):
                hallazgos.append(f"{ruta.name}: {texto!r}")
    assert hallazgos == []
