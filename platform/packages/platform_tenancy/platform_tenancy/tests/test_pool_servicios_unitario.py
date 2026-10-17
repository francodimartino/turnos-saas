"""Regresión: los servicios obtienen los tamaños de pool de TAMANOS_POOL, sin literales propios."""

import ast
from pathlib import Path

import pytest

from platform_tenancy import TAMANOS_POOL

RAIZ = next(p for p in Path(__file__).resolve().parents if (p / "apps").is_dir() and (p / "platform").is_dir())

# archivo -> servicios esperados, en orden de aparición
PUNTOS_DE_CREACION = {
    "apps/bot/bot_app/app.py": ["bot"],
    "apps/dashboard-backend/dashboard_backend/app.py": ["dashboard"],
    "apps/admin-backend/admin_backend/app.py": ["admin", "admin_readonly"],
    "apps/jobs/jobs_vencimiento/__main__.py": ["jobs"],
    "apps/jobs/jobs_purga/__main__.py": ["jobs"],
}


def _llamadas_crear_pool(ruta: str) -> list[ast.Call]:
    arbol = ast.parse((RAIZ / ruta).read_text(encoding="utf-8"))
    llamadas = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if nombre == "crear_pool":
                llamadas.append(nodo)
    return sorted(llamadas, key=lambda n: (n.lineno, n.col_offset))


@pytest.mark.parametrize("ruta,servicios", PUNTOS_DE_CREACION.items())
def test_cada_servicio_crea_su_pool_con_servicio_y_sin_tamanos_literales(ruta, servicios):
    llamadas = _llamadas_crear_pool(ruta)
    assert len(llamadas) == len(servicios), f"{ruta}: se esperaban {len(servicios)} creaciones de pool"
    for llamada, esperado in zip(llamadas, servicios, strict=True):
        claves = {k.arg for k in llamada.keywords}
        assert None not in claves, f"{ruta}: **kwargs impide verificar los tamaños"
        assert not claves & {"min_size", "max_size"}, f"{ruta}: tamaño literal en crear_pool"
        kw = next((k for k in llamada.keywords if k.arg == "servicio"), None)
        assert kw is not None, f"{ruta}: crear_pool sin servicio="
        assert isinstance(kw.value, ast.Constant) and kw.value.value == esperado
        assert esperado in TAMANOS_POOL


@pytest.mark.parametrize("ruta", PUNTOS_DE_CREACION)
def test_sin_min_size_max_size_literales_en_el_servicio(ruta):
    arbol = ast.parse((RAIZ / ruta).read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.keyword):
            assert nodo.arg not in ("min_size", "max_size"), f"{ruta}: {nodo.arg} literal"


@pytest.mark.parametrize("servicio", sorted(TAMANOS_POOL))
def test_crear_pool_entrega_los_tamanos_de_cada_servicio(monkeypatch, servicio):
    import asyncio
    import sys
    import types

    capturado = {}

    async def create_pool(dsn, **kwargs):
        capturado.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(create_pool=create_pool))
    from platform_tenancy import crear_pool

    asyncio.run(crear_pool("dsn", servicio=servicio))
    assert (capturado["min_size"], capturado["max_size"]) == TAMANOS_POOL[servicio]
