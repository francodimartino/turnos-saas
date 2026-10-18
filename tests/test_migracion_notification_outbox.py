"""Chequeos estáticos (sin base de datos) de la migración del outbox de notificaciones.

- La tabla tiene RLS, policy por tenant, FK compuesta y trigger de tenant_id.
- Ningún rol de aplicación tiene grants sobre la tabla; cada función es SECURITY DEFINER con search_path vacío y
  EXECUTE solo para los roles que la necesitan.
- La anonimización y la purga borran las filas del outbox.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "supabase" / "migrations" / "20260103002800_notification_outbox.sql"

EXECUTE_ESPERADO = {
    "enqueue_notification(uuid, text, text, jsonb, integer)": "bot_service, dashboard_service, scheduler_job",
    "complete_notification_outbox(uuid, text, text, integer)": "bot_service, dashboard_service, scheduler_job",
    "claim_notification_outbox(integer, integer, integer)": "scheduler_job",
    "prune_notification_outbox(integer)": "scheduler_job",
}


def _sql() -> str:
    lineas = [x for x in MIGRACION.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def test_la_tabla_tiene_rls_policy_fk_compuesta_y_trigger():
    sql = _sql()
    assert "alter table public.notification_outbox enable row level security" in sql
    assert "create policy tenant_isolation on public.notification_outbox" in sql
    assert "foreign key (tenant_id, end_customer_id) references public.end_customers (tenant_id, id)" in sql
    assert "unique (tenant_id, id)" in sql
    assert "create trigger notification_outbox_forzar_tenant_id before insert or update" in sql


def test_no_guarda_texto_ni_datos_del_destinatario():
    sql = _sql()
    tabla = sql[sql.index("create table public.notification_outbox") : sql.index("create index")]
    for prohibido in ("phone", "name text", "channel_user_id", "body", "message"):
        assert prohibido not in tabla


def test_ningun_grant_directo_sobre_la_tabla():
    sql = _sql()
    assert not re.search(r"grant [^;]* on (table )?public\.notification_outbox", sql)
    assert "revoke all on public.notification_outbox from public, anon, authenticated, service_role" in sql


def test_funciones_definer_con_search_path_y_execute_acotado():
    sql = _sql()
    for firma, roles in EXECUTE_ESPERADO.items():
        nombre = firma.split("(")[0]
        assert f"create function public.{nombre}(" in sql
        assert f"revoke all on function public.{firma} from public, anon, authenticated, service_role" in sql
        grants = re.findall(rf"grant execute on function public\.{re.escape(firma)} to ([^;]+)", sql)
        assert grants == [roles], firma
    assert sql.count("security definer set search_path = ''") >= len(EXECUTE_ESPERADO)


def test_el_reclamo_usa_skip_locked_y_arrienda_la_fila():
    sql = _sql()
    assert "for update skip locked" in sql
    assert "next_attempt_at = now() + make_interval(secs => p_lease_seconds)" in sql


def test_anonimizacion_y_purga_borran_el_outbox():
    sql = _sql()
    assert "delete from public.notification_outbox o where o.end_customer_id = p_id" in sql
    assert "delete from public.notification_outbox o where o.tenant_id = p_tenant_id" in sql
    assert "create or replace function public.anonymize_end_customer_core(" in sql
    assert "create or replace function public.purge_tenant_data(" in sql
    assert "grant execute on function public.purge_tenant_data(uuid) to purge_job" in sql
