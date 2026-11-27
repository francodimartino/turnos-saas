"""Chequeos estáticos (sin base de datos) de la migración del outbox multicanal.

- Solo depende de migraciones anteriores a su numeración (canales_cliente, avisos_negocio_config).
- Es aditiva: no borra columnas ni tablas.
- Las funciones son SECURITY DEFINER con search_path vacío y EXECUTE acotado; la interna no tiene ninguno.
- Conserva las columnas que ya existían (las usan las migraciones posteriores).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
MIGRACION = MIGRACIONES / "20260103004000_outbox_multicanal.sql"
NUMERO = MIGRACION.name.split("_")[0]

EXECUTE_ESPERADO = {
    "resolve_outbox_delivery(uuid)": "scheduler_job",
    "registrar_entrega_outbox(uuid, text, text, text)": "bot_service, dashboard_service, scheduler_job",
    "enqueue_notification_negocio(text, jsonb, integer)": "bot_service, dashboard_service, scheduler_job",
}


def _sql(ruta: Path = MIGRACION) -> str:
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def test_solo_depende_de_migraciones_anteriores():
    sql = _sql()
    for tabla in ("canales_cliente", "avisos_negocio_config"):
        assert f"public.{tabla}" in sql
        creadoras = [
            m.name.split("_")[0] for m in MIGRACIONES.glob("*.sql") if f"create table public.{tabla} " in _sql(m)
        ]
        assert creadoras and all(n < NUMERO for n in creadoras), tabla


def test_es_aditiva():
    sql = _sql()
    assert "drop table" not in sql
    assert "drop column" not in sql
    assert "truncate" not in sql
    assert re.findall(r"drop constraint (\w+)", sql) == ["notification_outbox_channel_check"]
    assert re.findall(r"alter column (\w+) drop not null", sql) == ["end_customer_id", "channel"]


def test_funciones_definer_con_search_path_y_execute_acotado():
    sql = _sql()
    for firma, roles in EXECUTE_ESPERADO.items():
        assert f"revoke all on function public.{firma} from public, anon, authenticated, service_role" in sql
        grants = re.findall(rf"grant execute on function public\.{re.escape(firma)} to ([^;]+)", sql)
        assert grants == [roles], firma
    interna = "outbox_destinatarios(uuid, uuid)"
    assert f"revoke all on function public.{interna} from public, anon, authenticated, service_role" in sql
    assert f"grant execute on function public.{interna}" not in sql
    assert sql.count("security definer set search_path = ''") >= 4


def test_el_check_de_plantillas_y_destino():
    sql = _sql()
    assert "constraint notification_outbox_destino_check check (destino in ('cliente', 'negocio'))" in sql
    assert "notification_outbox_plantilla_check" in sql
    for plantilla in (
        "aviso_negocio_reserva",
        "aviso_negocio_cancelacion",
        "aviso_negocio_movimiento",
        "aviso_negocio_aprobacion",
        "cambio_horario_aprobado",
        "cambio_horario_rechazado",
        "cambio_horario_vencido",
        "sincronizacion_calendar",
    ):
        assert f"'{plantilla}'" in sql, plantilla


def test_no_toca_el_reclamo_ni_el_cierre():
    sql = _sql()
    assert "function public.claim_notification_outbox" not in sql
    assert "function public.complete_notification_outbox" not in sql
    assert "function public.enqueue_notification(" not in sql
