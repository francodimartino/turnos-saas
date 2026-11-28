"""Tests unitarios del visor de notificaciones con problemas y limpiezas de Calendar fallidas."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from platform_admin import (
    DatosInvalidos,
    listar_limpiezas_calendar_fallidas,
    listar_notificaciones_con_problemas,
    visor_problemas_notificaciones,
)
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

UTC = timezone.utc  # noqa: UP017
ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _notif(status="failed"):
    return {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "end_customer_id": uuid.uuid4(),
        "channel": "telegram",
        "template": "booking_approved",
        "status": status,
        "retry_count": 3,
        "last_error": "timeout",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "sent_at": None,
    }


def _scrub():
    return {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "actor_type": "system",
        "actor_id": None,
        "action": "calendar_scrub_failed",
        "entity": "tenant_integrations",
        "entity_id": uuid.uuid4(),
        "metadata": json.dumps({"google_calendar_event_id": "ev1"}),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


async def test_notificaciones_con_problemas_filtra_failed_o_pending_viejo_con_umbral_de_una_hora():
    conn = ConexionFalsa({"from public.notifications_log": [_notif("failed"), _notif("pending")]})
    ahora = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    filas = await listar_notificaciones_con_problemas(PoolFalso(conn), actor_id=ACTOR, ahora=ahora)

    assert [f.status for f in filas] == ["failed", "pending"]
    sql, args = conn.con("from public.notifications_log")[0]
    assert "status = 'failed'" in sql and "status = 'pending'" in sql and "created_at" in sql
    assert args[0] == ahora - timedelta(hours=1)  # umbral calculado afuera, parametrizado
    assert "'sent'" not in sql


async def test_limpiezas_de_calendar_lee_solo_esas_acciones_con_metadata_decodificada():
    conn = ConexionFalsa({"from public.audit_log": [_scrub()]})
    filas = await listar_limpiezas_calendar_fallidas(PoolFalso(conn), actor_id=ACTOR)

    assert filas[0].action == "calendar_scrub_failed"
    assert filas[0].metadata == {"google_calendar_event_id": "ev1"}
    assert conn.con("from public.audit_log")[0][1][0] == [
        "calendar_scrub_failed",
        "calendar_scrub_retry_failed",
        "calendar_scrub_resolved",
        "calendar_scrub_discarded",
    ]


async def test_limpiezas_de_calendar_consulta_una_fila_por_evento_con_pendientes_primero():
    conn = ConexionFalsa({"from public.audit_log": [_scrub()]})
    await listar_limpiezas_calendar_fallidas(PoolFalso(conn), actor_id=ACTOR)

    sql = conn.con("from public.audit_log")[0][0]
    assert "partition by f.tenant_id, f.k" in sql and "rn = 1" in sql
    # pendientes antes que resueltas y descartadas, antes del limit
    assert "order by (action in ('calendar_scrub_resolved', 'calendar_scrub_discarded'))" in sql


async def test_visor_combina_ambas_listas_en_una_sola_transaccion():
    conn = ConexionFalsa({"from public.notifications_log": [_notif()], "from public.audit_log": [_scrub(), _scrub()]})
    pool = PoolFalso(conn)
    visor = await visor_problemas_notificaciones(pool, actor_id=ACTOR)

    assert len(visor.notificaciones) == 1 and len(visor.limpiezas_calendar) == 2
    assert len(pool.usadas) == 1


async def test_filtro_por_canal_se_manda_parametrizado_y_la_fila_trae_su_canal():
    fila = _notif("failed") | {"channel": "email"}
    conn = ConexionFalsa({"from public.notifications_log": [fila]})
    filas = await listar_notificaciones_con_problemas(PoolFalso(conn), actor_id=ACTOR, canal="email")

    assert [f.channel for f in filas] == ["email"]
    sql, args = conn.con("from public.notifications_log")[0]
    assert "channel = $3" in sql and args[2] == "email"


async def test_sin_filtro_de_canal_no_filtra_por_canal():
    conn = ConexionFalsa({"from public.notifications_log": [_notif("failed")]})
    await listar_notificaciones_con_problemas(PoolFalso(conn), actor_id=ACTOR)

    _, args = conn.con("from public.notifications_log")[0]
    assert args[2] is None


@pytest.mark.parametrize("canal", ["telegram", "whatsapp", "email"])
async def test_los_tres_canales_de_la_base_son_validos_tambien_en_el_visor_completo(canal):
    conn = ConexionFalsa({"from public.notifications_log": []})
    res = await visor_problemas_notificaciones(PoolFalso(conn), actor_id=ACTOR, canal=canal)

    assert res.notificaciones == []
    assert conn.con("from public.notifications_log")[0][1][2] == canal


@pytest.mark.parametrize("canal", ["sms", "EMAIL", "", " email", "email; drop table x", 3])
async def test_un_canal_desconocido_es_datos_invalidos_y_no_toca_la_base(canal):
    for funcion in (listar_notificaciones_con_problemas, visor_problemas_notificaciones):
        conn = ConexionFalsa()
        with pytest.raises(DatosInvalidos):
            await funcion(PoolFalso(conn), actor_id=ACTOR, canal=canal)
        assert conn.comandos == []
