"""Chequeos estaticos de la migracion que vuelve indexable la busqueda de audit_log_encadenar."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103002000_audit_log_encadenar_indexable.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _sql_plano() -> str:
    lineas = MIGRACION.read_text(encoding="utf-8").splitlines()
    return re.sub(r"\s+", " ", "\n".join(x for x in lineas if x.strip() and not x.lstrip().startswith("--")))


def test_plantilla_identica_a_la_migracion():
    assert PLANTILLA.read_text(encoding="utf-8") == MIGRACION.read_text(encoding="utf-8")


def test_busqueda_indexable_con_dos_ramas_y_sin_is_not_distinct_from():
    sql = _sql_plano()
    assert "is not distinct from" not in sql
    assert "where a.tenant_id = new.tenant_id order by a.chain_seq desc limit 1" in sql
    assert "where a.tenant_id is null order by a.chain_seq desc limit 1" in sql


def test_conserva_lock_hash_y_permisos():
    sql = _sql_plano()
    assert "pg_advisory_xact_lock(hashtextextended('audit_log_chain:' ||" in sql
    assert "public.audit_log_calcular_hash(v_ultimo_hash, new.id" in sql
    assert "revoke all on function public.audit_log_encadenar() from public, anon, authenticated, service_role;" in sql
    assert "drop " not in sql and "delete from" not in sql
