"""Chequeos estáticos de la migración de identidad del staff (nombre y email desde auth.users).

- Es de plataforma: idéntica a su plantilla en platform/infra/rls-policy-templates/.
- Función SECURITY DEFINER con search_path vacío, revoke explícito y EXECUTE solo a dos roles.
- Solo lee: no es destructiva.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
NOMBRE = "20260103000700_identidad_staff.sql"


def _sql() -> str:
    return (MIGRACIONES / NOMBRE).read_text(encoding="utf-8")


def test_existe_y_ordena_despues_de_las_previas():
    assert (MIGRACIONES / NOMBRE).is_file()
    assert "20260103000600_permisos_nuevos.sql" in {p.name for p in MIGRACIONES.glob("*.sql") if p.name < NOMBRE}


def test_es_identica_a_su_plantilla():
    assert (PLANTILLAS / NOMBRE).read_bytes() == (MIGRACIONES / NOMBRE).read_bytes()


def test_funcion_security_definer_con_search_path_vacio():
    sql = _sql()
    assert re.search(r"create function public\.get_tenant_staff_identity\(p_tenant_id uuid\)", sql, re.I)
    assert re.search(r"security definer", sql, re.I)
    assert re.search(r"set search_path = ''", sql, re.I)
    assert "raw_user_meta_data ->> 'full_name'" in sql or "raw_user_meta_data->>'full_name'" in sql


def test_revoke_explicito_y_execute_solo_a_dos_roles():
    sql = _sql()
    assert re.search(
        r"revoke all on function public\.get_tenant_staff_identity\(uuid\)\s+from public, anon, authenticated, service_role",
        sql,
        re.I,
    )
    grants = re.findall(r"grant execute on function public\.get_tenant_staff_identity\(uuid\)\s+to ([^;]+);", sql, re.I)
    assert len(grants) == 1
    assert {r.strip() for r in grants[0].split(",")} == {"dashboard_service", "platform_admin"}


def test_no_es_destructiva():
    assert not re.search(r"\b(drop|delete|truncate|update|insert)\b", _sql(), re.I)
