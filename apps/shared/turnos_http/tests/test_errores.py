"""Fija la forma de los errores HTTP de los backends: `{"detail": {"codigo", "mensaje"}}`."""

import ast
from pathlib import Path

from platform_auth import NoAutenticado, Prohibido

from turnos_http import (
    cuerpo_error,
    error_http,
    http_desde_error_auth,
    no_encontrado,
    respuesta_error,
)

PAQUETE = Path(__file__).resolve().parents[1]
PROHIBIDOS = {"turnos_bot", "dashboard_backend", "admin_backend"}


def test_error_http_lleva_codigo_y_mensaje():
    e = error_http(409, "duplicado", "Ya existe.")
    assert e.status_code == 409
    assert e.detail == {"codigo": "duplicado", "mensaje": "Ya existe."}
    assert e.headers is None


def test_error_http_acepta_campos_extra():
    e = error_http(409, "duplicado", "Ya existe.", id="x")
    assert e.detail == {"codigo": "duplicado", "mensaje": "Ya existe.", "id": "x"}


def test_no_encontrado_es_404():
    e = no_encontrado("El servicio")
    assert e.status_code == 404
    assert e.detail == {"codigo": "no_encontrado", "mensaje": "El servicio no existe."}


def test_cuerpo_y_respuesta_error():
    assert cuerpo_error("x", "y") == {"detail": {"codigo": "x", "mensaje": "y"}}
    r = respuesta_error(400, "x", "y")
    assert r.status_code == 400
    assert r.body == b'{"detail":{"codigo":"x","mensaje":"y"}}'


def test_error_auth_401_pide_bearer_y_el_resto_no():
    sin = http_desde_error_auth(NoAutenticado())
    assert sin.status_code == 401
    assert sin.headers == {"WWW-Authenticate": "Bearer"}
    assert set(sin.detail) == {"codigo", "mensaje"}
    prohibido = http_desde_error_auth(Prohibido())
    assert prohibido.status_code == 403
    assert prohibido.headers is None


def test_el_paquete_no_importa_codigo_de_las_apps():
    for ruta in PAQUETE.rglob("*.py"):
        if "tests" in ruta.relative_to(PAQUETE).parts:
            continue
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                nombres = {a.name.split(".")[0] for a in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
                nombres = {nodo.module.split(".")[0]}
            else:
                continue
            assert not nombres & PROHIBIDOS, f"{ruta.name} importa {nombres & PROHIBIDOS}"
