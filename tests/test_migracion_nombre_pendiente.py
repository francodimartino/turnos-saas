"""Chequeos estáticos de las migraciones del nombre de la invitación pendiente.

- La de columna/trigger/identidad es de plataforma: idéntica a su plantilla.
- La extensión de purge_tenant_data es del producto (parte de la versión vigente): sin plantilla.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
COLUMNA = "20260103000800_nombre_invitacion_pendiente.sql"
PURGA = "20260103000900_purga_nombre_pendiente.sql"


def _sql(nombre: str) -> str:
    return (MIGRACIONES / nombre).read_text(encoding="utf-8")


def test_ordenan_despues_de_la_identidad():
    assert COLUMNA > "20260103000700_identidad_staff.sql"
    assert PURGA > COLUMNA
    assert (MIGRACIONES / COLUMNA).is_file() and (MIGRACIONES / PURGA).is_file()


def test_la_migracion_de_columna_es_identica_a_su_plantilla():
    assert (PLANTILLAS / COLUMNA).read_bytes() == (MIGRACIONES / COLUMNA).read_bytes()


def test_la_purga_no_tiene_plantilla():
    assert not (PLANTILLAS / PURGA).exists()


def test_agrega_la_columna_nullable_de_texto():
    assert re.search(
        r"alter table public\.tenant_users\s+add column pending_invite_name text null", _sql(COLUMNA), re.I
    )


def test_no_da_grants_nuevos_a_dashboard_ni_readonly():
    assert not re.search(r"grant[^;]*pending_invite_name[^;]*to ", _sql(COLUMNA), re.I)


def test_el_trigger_cubre_la_columna_nueva_y_fija_search_path():
    sql = _sql(COLUMNA)
    assert re.search(r"create or replace function public\.tenant_users_limpiar_email_pendiente\(\)", sql, re.I)
    assert "new.pending_invite_name := null" in sql and "new.pending_invite_email := null" in sql
    assert re.search(r"set search_path = ''", sql)
    assert re.search(r"drop trigger tenant_users_limpiar_email_pendiente", sql, re.I)
    assert re.search(
        r"before insert or update of auth_user_id, pending_invite_email, pending_invite_name", sql, re.I
    )


def test_la_identidad_conserva_firma_grants_y_usa_el_nombre_pendiente():
    sql = _sql(COLUMNA)
    assert re.search(
        r"create or replace function public\.get_tenant_staff_identity\(p_tenant_id uuid\)\s+"
        r"returns table \(tenant_user_id uuid, email text, nombre text\)",
        sql,
        re.I,
    )
    assert re.search(r"security definer", sql, re.I) and re.search(r"set search_path = ''", sql)
    assert "coalesce((au.raw_user_meta_data ->> 'full_name')::text, tu.pending_invite_name)" in sql
    assert re.search(
        r"revoke all on function public\.get_tenant_staff_identity\(uuid\)\s+"
        r"from public, anon, authenticated, service_role",
        sql,
        re.I,
    )
    assert re.search(
        r"grant execute on function public\.get_tenant_staff_identity\(uuid\)\s+to dashboard_service, platform_admin",
        sql,
        re.I,
    )


def test_la_purga_reemplaza_con_la_misma_firma_y_grants():
    sql = _sql(PURGA)
    m = re.search(
        r"create or replace function public\.purge_tenant_data\((.*?)\)\s*returns setof uuid", sql, re.S | re.I
    )
    assert m and " ".join(m.group(1).split()) == "p_tenant_id uuid"
    assert re.search(r"security definer", sql, re.I) and re.search(r"set search_path = ''", sql)
    assert re.search(
        r"revoke all on function public\.purge_tenant_data\(uuid\) from public, anon, authenticated, service_role",
        sql,
        re.I,
    )
    assert re.search(r"grant execute on function public\.purge_tenant_data\(uuid\) to purge_job", sql, re.I)
    assert "pending_invite_name = null" in sql and "pending_invite_email = null" in sql
    for paso in ("anonymize_end_customers", "delete_bot_sessions", "delete_vault_secrets", "collect_auth_users"):
        assert paso in sql
