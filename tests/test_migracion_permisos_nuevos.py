"""Chequeos estáticos (sin base de datos) de la migración que siembra los permisos nuevos del catálogo.

- Es de plataforma: idéntica a su plantilla en platform/infra/rls-policy-templates/.
- Siembra manage_customers, delete_customers y manage_schedule, idempotente (on conflict do nothing).
- No toca las 5 claves originales ni borra nada.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
NOMBRE = "20260103000600_permisos_nuevos.sql"
NUEVAS = ("manage_customers", "delete_customers", "manage_schedule")


def _sql() -> str:
    return (MIGRACIONES / NOMBRE).read_text(encoding="utf-8")


def test_la_migracion_existe_y_ordena_despues_de_las_previas():
    assert (MIGRACIONES / NOMBRE).is_file()
    previas = {p.name for p in MIGRACIONES.glob("*.sql") if p.name < NOMBRE}
    assert "20260103000500_notifications_log_created_at.sql" in previas


def test_es_identica_a_su_plantilla():
    assert (PLANTILLAS / NOMBRE).read_bytes() == (MIGRACIONES / NOMBRE).read_bytes()


def test_siembra_las_tres_claves_de_forma_idempotente():
    sql = _sql()
    assert re.search(r"insert into public\.permissions \(key\)", sql, re.I)
    for clave in NUEVAS:
        assert f"('{clave}')" in sql
    assert re.search(r"on conflict \(key\) do nothing\s*;", sql, re.I)


def test_no_es_destructiva():
    sql = _sql()
    assert not re.search(r"\b(drop|delete|truncate|update)\b", sql, re.I)


def test_architecture_documenta_el_catalogo_completo():
    arq = (RAIZ / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for clave in ("view_bookings", "approve_bookings", "edit_prices", "manage_staff", *NUEVAS):
        assert f"`{clave}`" in arq, clave
