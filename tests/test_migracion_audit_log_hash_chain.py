"""Chequeos estaticos (sin base de datos) de la migracion de la cadena de hashes de audit_log."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001800_audit_log_hash_chain.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _sql_plano() -> str:
    lineas = MIGRACION.read_text(encoding="utf-8").splitlines()
    return re.sub(r"\s+", " ", "\n".join(x for x in lineas if x.strip() and not x.lstrip().startswith("--")))


def test_plantilla_identica_a_la_migracion():
    assert PLANTILLA.read_text(encoding="utf-8") == MIGRACION.read_text(encoding="utf-8")


def test_encadena_con_trigger_before_insert_y_lock_por_cadena():
    sql = _sql_plano()
    assert "create trigger audit_log_encadenar_row before insert on public.audit_log" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "create unique index audit_log_chain_tenant_uq" in sql
    assert "create unique index audit_log_chain_plataforma_uq" in sql


def test_verify_audit_chain_solo_para_platform_admin_con_search_path_vacio():
    sql = _sql_plano()
    assert (
        "revoke all on function public.verify_audit_chain(uuid) from public, anon, authenticated, service_role;" in sql
    )
    assert "grant execute on function public.verify_audit_chain(uuid) to platform_admin;" in sql
    assert "security definer set search_path = ''" in sql


def test_no_borra_ni_recalcula_datos_existentes():
    sql = _sql_plano().lower()
    assert "delete from" not in sql and "drop table" not in sql and "drop column" not in sql
    assert "update public.audit_log set chain_seq = v_seq, prev_hash = v_prev, hash = v_hash where id = r.id;" in sql
    # el trigger solo-agregar se vuelve a habilitar
    assert sql.index("disable trigger audit_log_solo_agregar_row") < sql.index(
        "enable trigger audit_log_solo_agregar_row"
    )
