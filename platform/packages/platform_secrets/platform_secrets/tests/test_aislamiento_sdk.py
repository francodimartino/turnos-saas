"""Las apps y el resto de `platform/` solo conocen la interfaz de platform_secrets (§1.6, regla 4).

Falla si cualquier archivo Python fuera del módulo de implementación importa el SDK de Supabase o
importa directamente la implementación sobre Vault.
"""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[5]
PAQUETE = RAIZ / "platform" / "packages" / "platform_secrets" / "platform_secrets"
IMPLEMENTACION = PAQUETE / "vault_supabase.py"
SDK = {"supabase", "supabase_auth", "supabase_functions", "gotrue", "postgrest", "realtime", "storage3", "supafunc"}
MODULO_IMPLEMENTACION = "platform_secrets.vault_supabase"


def importaciones_prohibidas(codigo: str) -> list[str]:
    """Módulos importados en `codigo` que son del SDK de Supabase o la implementación de Vault."""
    prohibidas = []
    for nodo in ast.walk(ast.parse(codigo)):
        if isinstance(nodo, ast.Import):
            nombres = [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            nombres = [nodo.module] + [f"{nodo.module}.{a.name}" for a in nodo.names]
        else:
            continue
        for nombre in nombres:
            if nombre.split(".")[0] in SDK or nombre == MODULO_IMPLEMENTACION:
                prohibidas.append(nombre)
    return prohibidas


def _fuentes_ajenas():
    for base in (RAIZ / "apps", RAIZ / "platform"):
        for ruta in base.rglob("*.py"):
            if "node_modules" in ruta.parts or ruta == IMPLEMENTACION:
                continue
            yield ruta


def test_detector_reconoce_el_sdk_y_la_implementacion():
    assert importaciones_prohibidas("import supabase") == ["supabase"]
    assert importaciones_prohibidas("from supabase import create_client")[0] == "supabase"
    assert importaciones_prohibidas("from gotrue import X") != []
    assert importaciones_prohibidas("from platform_secrets.vault_supabase import X") != []
    assert importaciones_prohibidas("import asyncpg\nfrom platform_secrets import crear_almacen") == []


def test_nada_fuera_de_la_implementacion_importa_el_sdk_ni_la_implementacion():
    infractores = {}
    for ruta in _fuentes_ajenas():
        if ruta.parent == PAQUETE and ruta.name == "__init__.py":
            continue  # el __init__ del propio paquete cablea la fábrica
        if ruta.parent == PAQUETE / "tests":
            continue  # los tests del propio detector citan los nombres como texto
        malas = importaciones_prohibidas(ruta.read_text(encoding="utf-8"))
        if malas:
            infractores[str(ruta.relative_to(RAIZ))] = malas
    assert infractores == {}


def test_la_implementacion_existe_y_tampoco_usa_el_sdk():
    assert IMPLEMENTACION.exists()
    assert [m for m in importaciones_prohibidas(IMPLEMENTACION.read_text(encoding="utf-8")) if m in SDK] == []
