"""Chequeos estáticos (sin base de datos) de la migración notifications_log.created_at.

- Es de plataforma: idéntica a su plantilla en platform/infra/rls-policy-templates/.
- Agrega la columna con default de la base e índice parcial de pendientes por antigüedad.
- Grants por columna: bot_service y dashboard_service no escriben created_at (default de la base).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"
NOMBRE = "20260103000500_notifications_log_created_at.sql"
COLUMNAS = "id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at"


def _sql() -> str:
    return (MIGRACIONES / NOMBRE).read_text(encoding="utf-8")


def test_la_migracion_existe_y_ordena_despues_de_las_previas():
    assert (MIGRACIONES / NOMBRE).is_file()
    previas = {p.name for p in MIGRACIONES.glob("*.sql") if p.name < NOMBRE}
    assert "20260103000300_purga_email_pendiente.sql" in previas


def test_es_identica_a_su_plantilla():
    assert (PLANTILLAS / NOMBRE).read_bytes() == (MIGRACIONES / NOMBRE).read_bytes()


def test_agrega_la_columna_not_null_con_default_now():
    assert re.search(
        r"alter table public\.notifications_log\s+add column created_at timestamptz not null default now\(\)",
        _sql(),
        re.I,
    )


def test_indice_parcial_de_pendientes_por_antiguedad():
    assert re.search(
        r"create index notifications_log_pending_created_at_idx\s+on public\.notifications_log \(created_at\)"
        r"\s+where status = 'pending'",
        _sql(),
        re.I,
    )


def test_grants_por_columna_sin_created_at_para_los_roles_de_aplicacion():
    sql = _sql()
    for rol in ("bot_service", "dashboard_service"):
        assert re.search(rf"revoke insert, update on public\.notifications_log from {rol}", sql, re.I)
        for priv in ("insert", "update"):
            assert re.search(
                rf"grant {priv} \({re.escape(COLUMNAS)}\)\s+on public\.notifications_log to {rol}", sql, re.I
            ), (rol, priv)
    assert not re.search(r"grant[^;]*created_at[^;]*to (bot_service|dashboard_service)", sql, re.I)
