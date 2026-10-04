"""Chequeos estáticos (sin base de datos) de las migraciones del email de invitación pendiente.

- La de columna/grants/trigger es de plataforma: idéntica a su plantilla en platform/infra/rls-policy-templates/.
- La extensión de purge_tenant_data es del producto (parte de la versión vigente, la de conexión): sin plantilla.
- Funciones nuevas sin EXECUTE por defecto y con search_path fijo; el reemplazo conserva la firma.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
COLUMNA = "20260103000200_email_invitacion_pendiente.sql"
PURGA = "20260103000300_purga_email_pendiente.sql"


def _sql(nombre: str) -> str:
    return (MIGRACIONES / nombre).read_text(encoding="utf-8")


def test_ordenan_despues_de_la_conexion_y_en_secuencia():
    assert COLUMNA > "20260103000100_conexion.sql"
    assert PURGA > COLUMNA
    assert (MIGRACIONES / COLUMNA).is_file() and (MIGRACIONES / PURGA).is_file()


def test_la_migracion_de_columna_es_identica_a_su_plantilla():
    assert (PLANTILLAS / COLUMNA).read_bytes() == (MIGRACIONES / COLUMNA).read_bytes()


def test_la_purga_no_tiene_plantilla():
    assert not (PLANTILLAS / PURGA).exists()


def test_agrega_la_columna_nullable_de_texto():
    sql = _sql(COLUMNA)
    assert re.search(r"alter table public\.tenant_users\s+add column pending_invite_email text null", sql, re.I)


def test_grants_por_columna_sin_la_nueva_para_dashboard_y_readonly():
    sql = _sql(COLUMNA)
    assert re.search(r"revoke select, insert, update on public\.tenant_users from dashboard_service", sql, re.I)
    assert re.search(r"revoke select on public\.tenant_users from platform_admin_readonly", sql, re.I)
    # los grants por columna que se devuelven no nombran pending_invite_email
    for m in re.finditer(r"grant (?:select|insert|update)[^;]*?\(([^)]*)\)[^;]*?to (dashboard_service|platform_admin_readonly)", sql, re.I):
        assert "pending_invite_email" not in m.group(1)
    assert not re.search(r"grant[^;]*pending_invite_email[^;]*to (dashboard_service|platform_admin_readonly)", sql, re.I)


def test_la_funcion_de_trigger_fija_search_path_y_revoca_execute():
    sql = _sql(COLUMNA)
    assert re.search(r"create function public\.tenant_users_limpiar_email_pendiente\(\)", sql, re.I)
    assert re.search(r"set search_path = ''", sql)
    assert re.search(
        r"revoke all on function public\.tenant_users_limpiar_email_pendiente\(\)\s+from public, anon, authenticated, service_role",
        sql,
        re.I,
    )


def test_la_purga_reemplaza_con_la_misma_firma_y_grants():
    sql = _sql(PURGA)
    m = re.search(r"create or replace function public\.purge_tenant_data\((.*?)\)\s*returns setof uuid", sql, re.S | re.I)
    assert m and " ".join(m.group(1).split()) == "p_tenant_id uuid"
    assert re.search(r"security definer", sql, re.I) and re.search(r"set search_path = ''", sql)
    assert re.search(
        r"revoke all on function public\.purge_tenant_data\(uuid\) from public, anon, authenticated, service_role", sql, re.I
    )
    assert re.search(r"grant execute on function public\.purge_tenant_data\(uuid\) to purge_job", sql, re.I)
    assert "pending_invite_email = null" in sql
    # conserva los cuatro pasos auditados de la versión vigente
    for paso in ("anonymize_end_customers", "delete_bot_sessions", "delete_vault_secrets", "collect_auth_users"):
        assert paso in sql
