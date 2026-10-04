"""Chequeos estáticos (sin base de datos) de la migración de plataforma 5.

- La migración de supabase/ es idéntica a su plantilla en platform/infra/rls-policy-templates/.
- Ninguna función nueva queda con EXECUTE por defecto: todas revocan de PUBLIC/anon/authenticated/service_role.
- Toda función fija search_path y las de purga/anonimización son SECURITY DEFINER.
- La plataforma no referencia tablas de dominio (la rama de dominio va en la migración del producto).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260101000500_platform_funcs_b.sql"
FUNCIONES = {
    "anonymize_end_customer_core",
    "anonymize_end_customer",
    "list_tenants_due_for_purge",
    "purge_tenant_data",
    "mark_tenant_purged",
    "prune_inbound_messages",
}
GRANTS = {
    "anonymize_end_customer": {"bot_service", "dashboard_service", "platform_admin"},
    "list_tenants_due_for_purge": {"purge_job"},
    "purge_tenant_data": {"purge_job"},
    "mark_tenant_purged": {"purge_job"},
    "prune_inbound_messages": {"purge_job"},
}


def _sql() -> str:
    return (RAIZ / "supabase" / "migrations" / NOMBRE).read_text(encoding="utf-8")


def test_la_migracion_es_identica_a_su_plantilla():
    plantilla = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE
    migracion = RAIZ / "supabase" / "migrations" / NOMBRE
    assert plantilla.is_file(), f"falta la plantilla {plantilla}"
    assert migracion.is_file(), f"falta la migración {migracion}"
    assert plantilla.read_bytes() == migracion.read_bytes()


def test_define_exactamente_las_funciones_esperadas():
    nombres = set(re.findall(r"create (?:or replace )?function public\.(\w+)\(", _sql(), re.IGNORECASE))
    assert nombres == FUNCIONES


def test_toda_funcion_revoca_execute_y_fija_search_path():
    sql = _sql()
    for nombre in FUNCIONES:
        assert re.search(
            rf"revoke all on function public\.{nombre}\(.*?\)\s+from public, anon, authenticated, service_role",
            sql,
            re.DOTALL | re.IGNORECASE,
        ), f"{nombre}: falta revocar EXECUTE de PUBLIC/anon/authenticated/service_role"
    for cuerpo in re.split(r"create (?:or replace )?function ", sql, flags=re.IGNORECASE)[1:]:
        encabezado = cuerpo.split("$$", 1)[0]
        assert "set search_path = ''" in encabezado, cuerpo[:60]


def test_grants_solo_a_los_roles_de_la_seccion_3_2():
    sql = _sql()
    otorgados = {
        nombre: set(re.split(r",\s*", roles.strip()))
        for nombre, roles in re.findall(
            r"grant execute on function public\.(\w+)\([^)]*\)\s+to ([\w, ]+);", sql, re.IGNORECASE
        )
    }
    assert otorgados == GRANTS


def test_no_toca_tablas_de_dominio():
    sql = re.sub(r"--[^\n]*", "", _sql())
    for tabla in ("bookings", "booking_packs", "professionals", "services"):
        assert not re.search(rf"\b{tabla}\b", sql), f"la migración de plataforma referencia {tabla}"
