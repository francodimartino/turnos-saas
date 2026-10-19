"""Patrón de capas de los backends: `rutas_*` solo traduce HTTP y los módulos de dominio no conocen FastAPI.

Un `rutas_*.py` válido define `router = APIRouter()` a nivel de módulo y sus endpoints son funciones de módulo; las
dependencias (pool, deps, config) se toman con `Depends` de `request.app.state`. Las fábricas `crear_router_*` y los
endpoints declarados dentro de una función son el patrón anterior: los archivos que todavía lo usan figuran en
`PENDIENTES_DE_CONVERTIR`, y esa lista solo puede achicarse (un archivo ya convertido que siga en ella falla).
"""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

METODOS_HTTP = {"get", "post", "put", "patch", "delete", "options", "head", "api_route", "websocket"}

# Archivos `rutas_*.py` que todavía usan fábricas `crear_router_*` o endpoints anidados. Cada conversión borra su línea.
PENDIENTES_DE_CONVERTIR = {
    "apps/admin-backend/admin_backend/rutas_auth.py",
    "apps/dashboard-backend/dashboard_backend/rutas_auth.py",
    "apps/dashboard-backend/dashboard_backend/rutas_calendar.py",
    "apps/dashboard-backend/dashboard_backend/rutas_clientes.py",
    "apps/dashboard-backend/dashboard_backend/rutas_staff.py",
    "apps/dashboard-backend/dashboard_backend/rutas_turnos.py",
}

# Módulos de dominio ya extraídos de su router: no importan fastapi (ni starlette). Cada conversión agrega el suyo.
MODULOS_DE_DOMINIO_CONVERTIDOS = {
    "apps/dashboard-backend/dashboard_backend/catalogo_asignaciones.py",
    "apps/dashboard-backend/dashboard_backend/catalogo_profesionales.py",
    "apps/dashboard-backend/dashboard_backend/catalogo_servicios.py",
    "apps/dashboard-backend/dashboard_backend/disponibilidad.py",
    "apps/dashboard-backend/dashboard_backend/settings_local.py",
}


def _archivos_rutas() -> list[Path]:
    return sorted(p for p in (RAIZ / "apps").rglob("rutas_*.py") if "node_modules" not in p.parts)


def _rel(p: Path) -> str:
    return p.relative_to(RAIZ).as_posix()


def _es_decorador_de_endpoint(dec: ast.expr) -> bool:
    llamada = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(llamada, ast.Attribute) and llamada.attr in METODOS_HTTP


def _incumplimientos(ruta: Path) -> list[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    problemas: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef):
            if nodo.name.startswith("crear_router_"):
                problemas.append(f"línea {nodo.lineno}: fábrica {nodo.name}")
            for interno in ast.walk(nodo):
                if interno is nodo or not isinstance(interno, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if any(_es_decorador_de_endpoint(d) for d in interno.decorator_list):
                    problemas.append(f"línea {interno.lineno}: endpoint {interno.name} dentro de {nodo.name}")
    return sorted(set(problemas))


def test_los_rutas_nuevos_o_convertidos_no_usan_fabricas_ni_endpoints_anidados():
    nuevos = {_rel(p): _incumplimientos(p) for p in _archivos_rutas() if _rel(p) not in PENDIENTES_DE_CONVERTIR}
    assert {k: v for k, v in nuevos.items() if v} == {}


def test_la_lista_de_pendientes_solo_tiene_archivos_que_siguen_incumpliendo():
    existentes = {_rel(p): p for p in _archivos_rutas()}
    inexistentes = PENDIENTES_DE_CONVERTIR - set(existentes)
    ya_convertidos = {r for r in PENDIENTES_DE_CONVERTIR & set(existentes) if not _incumplimientos(existentes[r])}
    assert inexistentes == set(), "archivos que ya no existen: sacarlos de la lista"
    assert ya_convertidos == set(), "archivos ya convertidos: sacarlos de la lista"


def test_el_detector_distingue_el_patron_viejo_del_nuevo(tmp_path):
    viejo = tmp_path / "rutas_x.py"
    viejo.write_text(
        "def crear_router_x(deps):\n    router = None\n    @router.get('/x')\n    async def ver():\n        return 1\n"
    )
    nuevo = tmp_path / "rutas_y.py"
    nuevo.write_text("router = None\n\n@router.get('/y')\nasync def ver():\n    return 1\n")
    assert len(_incumplimientos(viejo)) == 2
    assert _incumplimientos(nuevo) == []


def _importa_fastapi(ruta: Path) -> list[str]:
    encontrados = []
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        nombres = []
        if isinstance(nodo, ast.Import):
            nombres = [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres = [nodo.module]
        encontrados += [n for n in nombres if n.split(".")[0] in {"fastapi", "starlette"}]
    return encontrados


def test_los_modulos_de_dominio_convertidos_no_importan_fastapi():
    for rel in sorted(MODULOS_DE_DOMINIO_CONVERTIDOS):
        ruta = RAIZ / rel
        assert ruta.is_file(), f"{rel} no existe"
        assert _importa_fastapi(ruta) == [], rel
