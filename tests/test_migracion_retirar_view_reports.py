"""Chequeos estáticos de la migración que retira view_reports del catálogo.

- Idéntica a su plantilla de plataforma.
- Borra solo las asignaciones y la clave view_reports, en ese orden, sin tocar otras claves.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001900_retirar_view_reports.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _sql() -> str:
    return re.sub(r"--.*", "", MIGRACION.read_text(encoding="utf-8"))


def test_es_identica_a_su_plantilla():
    assert PLANTILLA.read_bytes() == MIGRACION.read_bytes()


def test_borra_asignaciones_antes_que_la_clave():
    sql = _sql()
    asign = sql.index("delete from public.tenant_user_permissions where permission_key = 'view_reports'")
    clave = sql.index("delete from public.permissions where key = 'view_reports'")
    assert asign < clave


def test_solo_toca_view_reports():
    sql = _sql()
    assert not re.search(r"\b(drop|truncate|update|insert)\b", sql, re.I)
    assert len(re.findall(r"\bdelete\b", sql, re.I)) == 2
    assert sql.count("'view_reports'") == 2
