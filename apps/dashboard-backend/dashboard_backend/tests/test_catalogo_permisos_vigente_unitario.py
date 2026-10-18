"""Los catálogos falsos de los tests solo usan claves del seed vigente de `public.permissions`.

El seed vigente se deriva de las migraciones (altas menos retiros), así que si un permiso se retira de la base
y un test sigue simulándolo, este test falla. Además, la API de staff rechaza una clave retirada.
"""

import ast
import re
import uuid
from pathlib import Path

from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, http, token
from dashboard_backend.tests.soporte_staff import ConnStaff, app_staff

RAIZ = Path(__file__).resolve().parents[4]
MIGRACIONES = RAIZ / "supabase" / "migrations"
VIGENTES_ESPERADOS = {
    "view_bookings",
    "approve_bookings",
    "edit_prices",
    "manage_staff",
    "manage_customers",
    "delete_customers",
    "manage_schedule",
}
RETIRADO = "view_reports"


def _claves(texto: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", texto))


def _seed_vigente() -> set[str]:
    claves: set[str] = set()
    for archivo in sorted(MIGRACIONES.glob("*.sql")):
        sql = archivo.read_text(encoding="utf-8")
        for m in re.finditer(r"insert into public\.permissions \(key\)\s*values(.*?)on conflict", sql, re.S | re.I):
            claves |= _claves(m.group(1))
        for m in re.finditer(r"delete from public\.permissions where key = '([a-z_]+)'", sql, re.I):
            claves.discard(m.group(1))
    return claves


def test_el_seed_vigente_es_el_esperado():
    assert _seed_vigente() == VIGENTES_ESPERADOS


def test_los_catalogos_falsos_instanciados_usan_claves_del_seed():
    vigente = _seed_vigente()
    assert set(ConnFalsa().catalogo_permisos) <= vigente
    assert set(ConnStaff().catalogo_permisos) <= vigente


def test_ningun_test_del_backend_ni_de_platform_auth_lista_claves_fuera_del_seed():
    """Recorre las listas/tuplas/sets de constantes asignadas a nombres de catálogo o permisos."""
    vigente = _seed_vigente()
    carpetas = [Path(__file__).parent, RAIZ / "platform" / "packages" / "platform_auth" / "platform_auth" / "tests"]
    fuera: list[str] = []
    for carpeta in carpetas:
        for archivo in carpeta.glob("*.py"):
            if archivo == Path(__file__):
                continue
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.Assign | ast.AnnAssign):
                    continue
                objetivos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
                nombres = [ast.unparse(t).lower() for t in objetivos]
                if not any("catalogo" in n or "permisos_base" in n for n in nombres):
                    continue
                if not isinstance(nodo.value, ast.List | ast.Tuple | ast.Set):
                    continue
                for hijo in ast.walk(nodo.value):
                    if isinstance(hijo, ast.Constant) and isinstance(hijo.value, str) and hijo.value not in vigente:
                        fuera.append(f"{archivo.name}:{hijo.lineno} {hijo.value}")
    assert fuera == []


async def test_otorgar_view_reports_por_la_api_de_staff_es_422_permiso_desconocido():
    app, conn = app_staff()
    destino = conn.agregar(permisos=("view_bookings",))
    conn.estado_staff = {"is_owner": True, "is_active": True, "tiene": True}
    cabeceras = {
        "Cookie": "dash_acceso=" + token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), is_owner=True, aal="aal2"),
        "Origin": ORIGEN,
    }
    r = await http(app).put(
        f"/staff/{destino}/permisos", json={"permission_keys": ["view_bookings", RETIRADO]}, headers=cabeceras
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "permiso_desconocido"
    assert conn.sentencias_de_escritura() == []
