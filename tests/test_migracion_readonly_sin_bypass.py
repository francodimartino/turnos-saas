"""Chequeos estáticos (sin base de datos) de la migración que quita el bypass de platform_admin_readonly.

- Recrea (alter policy) tenant_isolation en las 16 tablas con tenant, sin mencionar al rol readonly.
- Ninguna migración posterior a la original vuelve a introducir el bypass.
- La plantilla de plataforma cubre las tablas de plataforma.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
NOMBRE = "20260103001100_readonly_sin_bypass_rls.sql"

PLATAFORMA = {
    "tenants",
    "tenant_users",
    "tenant_user_permissions",
    "tenant_integrations",
    "end_customers",
    "audit_log",
    "inbound_messages",
    "bot_sessions",
    "notifications_log",
    "payments",
}
TURNOS = {
    "professionals",
    "availability_rules",
    "availability_blocks",
    "services",
    "professional_services",
    "bookings",
    "booking_packs",
}


def _politicas(sql: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"alter policy tenant_isolation on public\.(\w+)\s+using \((.*?)\);", sql, re.S)
    }


def test_la_migracion_cubre_todas_las_tablas_con_tenant():
    politicas = _politicas((MIGRACIONES / NOMBRE).read_text(encoding="utf-8"))
    assert set(politicas) == PLATAFORMA | TURNOS


def test_las_policies_no_mencionan_al_rol_readonly_ni_current_user():
    for ruta in (MIGRACIONES / NOMBRE, PLANTILLAS / NOMBRE):
        for tabla, expr in _politicas(ruta.read_text(encoding="utf-8")).items():
            assert "readonly" not in expr and "current_user" not in expr, (ruta.name, tabla)
            assert "app.current_tenant_id" in expr, (ruta.name, tabla)


def test_la_plantilla_cubre_las_tablas_de_plataforma():
    assert set(_politicas((PLANTILLAS / NOMBRE).read_text(encoding="utf-8"))) == PLATAFORMA


def test_ninguna_migracion_posterior_reintroduce_el_bypass():
    for ruta in MIGRACIONES.glob("*.sql"):
        if ruta.name > NOMBRE:
            assert "current_user = 'platform_admin_readonly'" not in ruta.read_text(encoding="utf-8"), ruta.name
