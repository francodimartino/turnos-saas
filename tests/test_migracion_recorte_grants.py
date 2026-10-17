"""Chequeos estáticos (sin base de datos) de la migración que recorta grants de más.

- Revoca lo que platform_admin, bot_service y dashboard_service no usan.
- No toca a scheduler_job (conserva find_integration y get_integration_secret) ni a purge_job.
- Otorga record_notification_attempt a bot_service y dashboard_service sin quitar grants de notifications_log.
- La plantilla de plataforma coincide con la migración salvo el grant de turnos.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001200_recorte_grants.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _cuerpo(ruta: Path) -> list[str]:
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    return [x for x in lineas if x.strip() and not x.lstrip().startswith("--")]


def _sql_plano(ruta: Path) -> str:
    return re.sub(r"\s+", " ", "\n".join(_cuerpo(ruta)))


def test_revoca_lo_que_platform_admin_no_usa():
    sql = _sql_plano(MIGRACION)
    assert "revoke insert, delete on public.platform_admins from platform_admin;" in sql
    assert "revoke execute on function public.get_integration_secret(uuid) from platform_admin;" in sql
    assert "revoke execute on function public.anonymize_end_customer(uuid) from platform_admin;" in sql


def test_recorta_payments_de_bot_y_audit_log_de_dashboard():
    sql = _sql_plano(MIGRACION)
    assert "revoke all on public.payments from bot_service;" in sql
    assert "revoke select on public.audit_log from dashboard_service;" in sql


def test_no_toca_a_scheduler_job_ni_a_purge_job_en_los_revoke():
    for sentencia in _sql_plano(MIGRACION).split(";"):
        if sentencia.strip().startswith("revoke"):
            assert "scheduler_job" not in sentencia and "purge_job" not in sentencia, sentencia


def test_otorga_record_notification_attempt_sin_quitar_notifications_log():
    sql = _sql_plano(MIGRACION)
    assert (
        "grant execute on function public.record_notification_attempt(uuid, text, text, text, integer, text, uuid) "
        "to bot_service, dashboard_service;"
    ) in sql
    assert not re.search(r"revoke[^;]*notifications_log", sql)


def test_revoca_las_funciones_trigger_a_los_roles_de_supabase():
    sql = _sql_plano(MIGRACION)
    assert "prorettype = 'trigger'::regtype" in sql
    assert "revoke all on function %s from public, anon, authenticated, service_role" in sql


def test_la_plantilla_es_la_migracion_sin_el_grant_de_turnos():
    assert "record_notification_attempt" not in "\n".join(_cuerpo(PLANTILLA))
    assert _cuerpo(MIGRACION)[:-2] == _cuerpo(PLANTILLA)
