"""Endpoints /turnos y /packs sin base real: permisos, estados, Calendar, avisos al cliente y validación.

La base (exclusion constraint, triggers de transición y de pack, RLS) se prueba contra Postgres real en
`test_turnos_integracion.py`. Acá la conexión es falsa y Telegram/Calendar van simulados.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import asyncpg.exceptions as pgx
import pytest
from platform_calendar import ErrorApiCalendar, ErrorConfiguracionCalendar
from platform_notifications import IntegracionNoConfigurada
from turnos_queries import consultas as q

from dashboard_backend import turnos as t
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_turnos import (
    CHAT_ID,
    DURACION,
    INICIO,
    TOKEN_BOT,
    BotSimulado,
    CalendarioSimulado,
    ConnTurnos,
    app_turnos,
    fabrica_de,
    fila_turno,
)

TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
NUEVO_INICIO = INICIO + timedelta(days=1)


def _h(escribe=True, user=STAFF):
    h = {"Cookie": "dash_acceso=" + token(user_id=user, tenant_id=TENANT)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _turno(conn: ConnTurnos, **cambios):
    fila = fila_turno(TENANT, **cambios)
    conn.turnos[fila["id"]] = fila
    return fila


def _acciones(conn):
    return [a[0] for a in conn.auditoria]


def _permisos_consultados(conn):
    return [args[2] for sql, args in conn.ejecutado if "tenant_user_permissions" in sql]


def _alta_ok(conn, **cambios):
    datos = {
        "duration_minutes": 30,
        "service_name": "Corte",
        "service_active": True,
        "professional_active": True,
        "ofrece": True,
        "customer_name": "Cliente de prueba",
        "customer_phone": "+5491155500101",
        "customer_deleted_at": None,
    }
    datos.update(cambios)
    conn.datos_alta = datos


def _cuerpo_alta(**cambios):
    cuerpo = {
        "end_customer_id": str(uuid.uuid4()),
        "service_id": str(uuid.uuid4()),
        "professional_id": str(uuid.uuid4()),
        "start_at": INICIO.isoformat(),
    }
    cuerpo.update(cambios)
    return cuerpo


# --- permisos por endpoint ----------------------------------------------------------------------------------

_ID = uuid.uuid4()
ENDPOINTS = [
    ("get", "/turnos?desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z", None, "view_bookings"),
    ("get", f"/turnos/{_ID}", None, "view_bookings"),
    ("get", "/packs", None, "view_bookings"),
    ("post", "/turnos", _cuerpo_alta(), "approve_bookings"),
    ("post", f"/turnos/{_ID}/aprobar", None, "approve_bookings"),
    ("post", f"/turnos/{_ID}/rechazar", None, "approve_bookings"),
    ("post", f"/turnos/{_ID}/cancelar", None, "approve_bookings"),
    ("post", f"/turnos/{_ID}/mover", {"start_at": NUEVO_INICIO.isoformat()}, "approve_bookings"),
    ("post", f"/turnos/{_ID}/completar", None, "approve_bookings"),
    (
        "post",
        "/packs",
        {"end_customer_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()), "total_sessions": 5},
        "approve_bookings",
    ),
]


async def _llamar(app, metodo, ruta, cuerpo, headers):
    kw = {"json": cuerpo} if cuerpo else {}
    return await getattr(http(app), metodo)(ruta, headers=headers, **kw)


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_sin_el_permiso_el_endpoint_responde_403_y_no_toca_turnos(metodo, ruta, cuerpo, permiso):
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    r = await _llamar(app, metodo, ruta, cuerpo, _h(escribe=metodo != "get"))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    # Aprobar/rechazar pueden leer el turno para ver si es del profesional vinculado, pero nunca escriben.
    assert not conn.sentencias_que_contienen("update public.booking")
    assert not conn.sentencias_que_contienen("insert into public.booking")
    assert not conn.auditoria


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_el_permiso_que_se_consulta_es_el_documentado(metodo, ruta, cuerpo, permiso):
    app, conn, _, _ = app_turnos()
    await _llamar(app, metodo, ruta, cuerpo, _h(escribe=metodo != "get"))
    assert _permisos_consultados(conn)[0] == permiso


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_sin_sesion_es_401(metodo, ruta, cuerpo, permiso):
    app, _, _, _ = app_turnos()
    r = await _llamar(app, metodo, ruta, cuerpo, {"Origin": ORIGEN})
    assert r.status_code == 401


@pytest.mark.parametrize("ruta", [f"/turnos/{_ID}", f"/packs/{_ID}"])
async def test_no_existe_ninguna_ruta_delete(ruta):
    app, conn, _, _ = app_turnos()
    r = await http(app).delete(ruta, headers=_h())
    assert r.status_code in (404, 405)
    assert not conn.sentencias_que_contienen("delete")


async def test_el_profesional_vinculado_aprueba_sin_permiso_de_aprobar():
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    fila = _turno(conn, professional_auth_user_id=STAFF)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    assert conn.turnos[fila["id"]]["status"] == "confirmed"


async def test_el_profesional_vinculado_tambien_puede_rechazar():
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    fila = _turno(conn, professional_auth_user_id=STAFF)
    r = await http(app).post(f"/turnos/{fila['id']}/rechazar", headers=_h())
    assert r.status_code == 200
    assert conn.turnos[fila["id"]]["status"] == "cancelled"


async def test_un_profesional_vinculado_a_otra_cuenta_recibe_403_igual_que_si_el_turno_no_existe():
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    ajeno = _turno(conn, professional_auth_user_id=uuid.uuid4())
    for turno_id in (ajeno["id"], uuid.uuid4()):
        r = await http(app).post(f"/turnos/{turno_id}/aprobar", headers=_h())
        assert r.status_code == 403
        assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert conn.turnos[ajeno["id"]]["status"] == "pending"


@pytest.mark.parametrize("accion", ["cancelar", "completar"])
async def test_ser_el_profesional_vinculado_no_alcanza_para_cancelar_ni_completar(accion):
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    fila = _turno(conn, status="confirmed", pending_expires_at=None, professional_auth_user_id=STAFF)
    r = await http(app).post(f"/turnos/{fila['id']}/{accion}", headers=_h())
    assert r.status_code == 403


async def test_ser_el_profesional_vinculado_no_alcanza_para_mover():
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    fila = _turno(conn, status="confirmed", pending_expires_at=None, professional_auth_user_id=STAFF)
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
    assert r.status_code == 403


# --- aprobar -------------------------------------------------------------------------------------------------


async def test_aprobar_confirma_audita_crea_el_evento_y_avisa_al_cliente():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "confirmed"
    assert cuerpo["google_calendar_event_id"] == "evt-nuevo"
    assert cuerpo["calendario"] == {"estado": "creado"}
    assert cuerpo["aviso"] == "programado"
    assert "tenant_id" not in cuerpo
    assert conn.turnos[fila["id"]]["google_calendar_event_id"] == "evt-nuevo"
    assert conn.ejecuciones(t.SQL_CAMBIAR_ESTADO) == [(TENANT, fila["id"], "confirmed")]
    assert conn.auditoria[0][:3] == ("booking_approved", "bookings", fila["id"])
    assert len(calendario.llamadas) == 1 and calendario.llamadas[0].status == "confirmed"
    assert calendario.llamadas[0].google_calendar_event_id is None
    assert len(bot.mensajes) == 1
    assert bot.mensajes[0]["chat_id"] == CHAT_ID
    assert "confirmado" in bot.mensajes[0]["text"]


async def test_el_calendario_se_toca_solo_despues_del_commit_del_cambio():
    app, conn, calendario, _ = app_turnos()
    fila = _turno(conn)
    await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    previas = calendario.sentencias_al_sincronizar
    assert t.SQL_CAMBIAR_ESTADO in previas
    assert "COMMIT" in previas[previas.index(t.SQL_CAMBIAR_ESTADO) :]


async def test_aprobar_un_turno_ya_confirmado_cancelado_o_completado_es_409_sin_efectos():
    app, conn, calendario, bot = app_turnos()
    for estado in ("confirmed", "cancelled", "completed"):
        fila = _turno(conn, status=estado, pending_expires_at=None)
        r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
        assert r.status_code == 409
        assert r.json()["detail"]["codigo"] == "estado_invalido"
    assert not conn.ejecuciones(t.SQL_CAMBIAR_ESTADO)
    assert not calendario.llamadas and not bot.mensajes and not conn.auditoria


async def test_aprobar_un_pendiente_vencido_es_409():
    app, conn, calendario, _ = app_turnos()
    fila = _turno(conn, vencido=True)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "turno_vencido"
    assert not calendario.llamadas


async def test_un_turno_inexistente_o_de_otro_tenant_es_404():
    app, conn, calendario, _ = app_turnos()
    ajeno = fila_turno(uuid.uuid4())
    conn.turnos[ajeno["id"]] = ajeno
    for turno_id in (uuid.uuid4(), ajeno["id"]):
        for accion in ("aprobar", "rechazar", "cancelar", "completar"):
            r = await http(app).post(f"/turnos/{turno_id}/{accion}", headers=_h())
            assert r.status_code == 404, (accion, r.text)
        r = await http(app).post(f"/turnos/{turno_id}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
        assert r.status_code == 404
    assert not calendario.llamadas and ajeno["status"] == "pending"


# --- rechazar ------------------------------------------------------------------------------------------------


async def test_rechazar_cancela_avisa_y_nunca_toca_el_calendario():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/rechazar", headers=_h())
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert conn.ejecuciones(t.SQL_CAMBIAR_ESTADO) == [(TENANT, fila["id"], "cancelled")]
    assert _acciones(conn) == ["booking_rejected"]
    assert not calendario.llamadas
    assert len(bot.mensajes) == 1 and "rechaz" in bot.mensajes[0]["text"]


async def test_rechazar_solo_sirve_para_pendientes():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None)
    r = await http(app).post(f"/turnos/{fila['id']}/rechazar", headers=_h())
    assert r.status_code == 409


# --- cancelar ------------------------------------------------------------------------------------------------


async def test_cancelar_un_confirmado_borra_el_evento_y_avisa():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None, google_calendar_event_id="evt-1")
    r = await http(app).post(f"/turnos/{fila['id']}/cancelar", headers=_h())
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["status"] == "cancelled"
    assert cuerpo["google_calendar_event_id"] is None
    assert cuerpo["calendario"] == {"estado": "borrado"}
    assert conn.turnos[fila["id"]]["google_calendar_event_id"] is None
    assert calendario.llamadas[0].status == "cancelled" and calendario.llamadas[0].google_calendar_event_id == "evt-1"
    assert _acciones(conn) == ["booking_cancelled"]
    assert "cancel" in bot.mensajes[0]["text"]


async def test_cancelar_no_hace_delete_y_un_turno_ya_cancelado_o_completado_es_409():
    app, conn, calendario, _ = app_turnos()
    for estado in ("cancelled", "completed"):
        fila = _turno(conn, status=estado, pending_expires_at=None)
        r = await http(app).post(f"/turnos/{fila['id']}/cancelar", headers=_h())
        assert r.status_code == 409
    assert not conn.sentencias_que_contienen("delete from")
    assert not calendario.llamadas


async def test_cancelar_un_pendiente_es_valido_y_no_llama_al_calendario():
    app, conn, calendario, _ = app_turnos()
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/cancelar", headers=_h())
    assert r.status_code == 200
    assert not calendario.llamadas


# --- completar -----------------------------------------------------------------------------------------------


async def test_completar_un_confirmado_no_avisa_ni_toca_el_calendario():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None, google_calendar_event_id="evt-1")
    r = await http(app).post(f"/turnos/{fila['id']}/completar", headers=_h())
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert conn.ejecuciones(t.SQL_CAMBIAR_ESTADO) == [(TENANT, fila["id"], "completed")]
    assert _acciones(conn) == ["booking_completed"]
    assert not calendario.llamadas and not bot.mensajes


@pytest.mark.parametrize("estado", ["pending", "cancelled", "completed"])
async def test_completar_solo_sirve_para_confirmados(estado):
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, status=estado, pending_expires_at=INICIO if estado == "pending" else None)
    r = await http(app).post(f"/turnos/{fila['id']}/completar", headers=_h())
    assert r.status_code == 409


# --- mover ---------------------------------------------------------------------------------------------------


async def test_mover_actualiza_el_mismo_turno_conserva_la_duracion_y_actualiza_el_evento():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None, google_calendar_event_id="evt-1")
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
    assert r.status_code == 200, r.text
    assert conn.ejecuciones(t.SQL_MOVER) == [(TENANT, fila["id"], NUEVO_INICIO, NUEVO_INICIO + DURACION)]
    assert not conn.ejecuciones(t.SQL_INSERTAR_TURNO)
    assert r.json()["id"] == str(fila["id"])
    assert r.json()["calendario"] == {"estado": "actualizado"}
    llamada = calendario.llamadas[0]
    assert (llamada.inicio, llamada.fin, llamada.google_calendar_event_id) == (
        NUEVO_INICIO,
        NUEVO_INICIO + DURACION,
        "evt-1",
    )
    assert _acciones(conn) == ["booking_moved"]
    assert "movi" in bot.mensajes[0]["text"] or "reprogram" in bot.mensajes[0]["text"]


async def test_mover_a_un_horario_ocupado_es_409_y_no_toca_calendario_ni_avisa():
    app, conn, calendario, bot = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None, google_calendar_event_id="evt-1")
    conn.fallas[t.SQL_MOVER] = pgx.ExclusionViolationError("conflicting key value violates exclusion constraint")
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "horario_ocupado"
    assert "bookings_sin_doble_booking" not in r.text
    assert "ROLLBACK" in conn.sentencias()
    assert not calendario.llamadas and not bot.mensajes and not conn.auditoria


async def test_mover_un_pendiente_es_409_y_una_fecha_sin_zona_es_422():
    app, conn, _, _ = app_turnos()
    pendiente = _turno(conn)
    r = await http(app).post(
        f"/turnos/{pendiente['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h()
    )
    assert r.status_code == 409
    confirmado = _turno(conn, status="confirmed", pending_expires_at=None)
    r = await http(app).post(
        f"/turnos/{confirmado['id']}/mover", json={"start_at": "2030-03-05T10:00:00"}, headers=_h()
    )
    assert r.status_code == 422
    assert not conn.ejecuciones(t.SQL_MOVER)


# --- Calendar que falla --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [ErrorApiCalendar(500), RuntimeError("detalle interno"), ErrorConfiguracionCalendar("falta")]
)
async def test_un_fallo_de_calendar_no_revierte_el_turno_ni_devuelve_500_y_queda_registrado(error):
    conn = ConnTurnos()
    fabrica = fabrica_de(error_al_armar=error) if isinstance(error, ErrorConfiguracionCalendar) else None
    calendario = None if fabrica else CalendarioSimulado(conn, error=error)
    app, conn, _, bot = app_turnos(conn=conn, calendario=calendario, fabrica=fabrica)
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["calendario"]["estado"] == "error"
    assert "detalle interno" not in r.text
    assert conn.turnos[fila["id"]]["status"] == "confirmed"
    fallos = [a for a in conn.auditoria if a[0] == "calendar_sync_failed"]
    assert len(fallos) == 1
    assert fallos[0][2] == fila["id"]
    assert "detalle interno" not in str(fallos[0])
    assert len(bot.mensajes) == 1  # el cliente igual se entera de que su turno fue confirmado


# --- avisos al cliente ---------------------------------------------------------------------------------------


async def test_un_cliente_anonimizado_no_recibe_aviso_ni_se_pierde_el_cambio():
    app, conn, _, bot = app_turnos()
    fila = _turno(
        conn,
        customer_deleted_at=INICIO,
        customer_channel=None,
        customer_channel_user_id=None,
        customer_name=None,
        customer_phone=None,
    )
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert r.json()["aviso"] == "cliente_anonimizado"
    assert r.json()["sin_cliente_identificable"] is True
    assert not bot.mensajes


async def test_un_cliente_sin_canal_no_recibe_aviso():
    app, conn, _, bot = app_turnos()
    fila = _turno(conn, customer_channel=None, customer_channel_user_id=None)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert r.json()["aviso"] == "sin_canal"
    assert not bot.mensajes


async def test_un_canal_sin_adapter_no_recibe_aviso():
    app, conn, _, bot = app_turnos()
    fila = _turno(conn, customer_channel="whatsapp", customer_channel_user_id="5491155500101")
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.json()["aviso"] == "canal_no_soportado"
    assert not bot.mensajes


async def test_sin_integracion_de_bot_el_cambio_se_hace_y_no_se_avisa():
    app, conn, _, bot = app_turnos(bot=_bot_sin_integracion())
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert r.json()["aviso"] == "sin_integracion"
    assert conn.turnos[fila["id"]]["status"] == "confirmed"
    assert not bot.mensajes


def _bot_sin_integracion():
    class _Bot(BotSimulado):
        def opciones(self, **kw):
            return super().opciones(sin_integracion=IntegracionNoConfigurada("sin bot"))

    return _Bot()


async def test_el_aviso_se_registra_en_notifications_log_sin_guardar_el_texto():
    app, conn, _, bot = app_turnos()
    fila = _turno(conn)
    await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    llamadas = [a for s, a in conn.ejecutado if "record_notification_attempt" in s]
    crear = [a for a in llamadas if a[6] is None]
    assert len(crear) == 1
    args = crear[0]
    assert args[0] == fila["end_customer_id"] and args[1] == "telegram" and args[2] == "turno_confirmado"
    assert all("fue confirmado" not in str(a) for a in args)
    assert any(a[6] is not None and a[3] == "sent" for a in llamadas)  # pasa a 'sent'


async def test_si_telegram_falla_el_turno_queda_cambiado_y_el_envio_agota_reintentos_como_failed():
    app, conn, _, bot = app_turnos(bot=BotSimulado(estado=500))
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert conn.turnos[fila["id"]]["status"] == "confirmed"
    assert len(bot.mensajes) == 4  # primer intento + 3 reintentos
    estados = [a[3] for s, a in conn.ejecutado if "record_notification_attempt" in s]
    assert "failed" in estados


def _outbox(conn, funcion):
    return [(i, a) for i, (s, a) in enumerate(conn.ejecutado) if funcion in s]


@pytest.mark.parametrize(
    ("ruta", "plantilla", "cambios"),
    [
        ("aprobar", "turno_confirmado", {}),
        ("rechazar", "turno_rechazado", {}),
        ("cancelar", "turno_cancelado", {"status": "confirmed", "pending_expires_at": None}),
    ],
)
async def test_el_aviso_se_encola_en_la_misma_transaccion_del_cambio_y_antes_del_commit(ruta, plantilla, cambios):
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, **cambios)
    r = await http(app).post(f"/turnos/{fila['id']}/{ruta}", headers=_h())
    assert r.status_code == 200, r.text
    ((posicion, args),) = _outbox(conn, "enqueue_notification")
    assert args[0] == fila["end_customer_id"] and args[1] == "telegram" and args[2] == plantilla
    assert json.loads(args[3])["booking_id"] == str(fila["id"])
    assert "Corte" not in args[3] and CHAT_ID not in args[3]  # sin texto ni destinatario
    sentencias = conn.sentencias()
    cambio = sentencias.index(t.SQL_CAMBIAR_ESTADO)
    assert cambio < posicion < sentencias.index("COMMIT", posicion)
    assert "COMMIT" not in sentencias[cambio:posicion]  # misma transacción que el cambio


async def test_mover_encola_el_aviso_con_el_nuevo_horario_en_la_misma_transaccion():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None)
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
    assert r.status_code == 200, r.text
    ((posicion, args),) = _outbox(conn, "enqueue_notification")
    assert args[2] == "turno_movido"
    assert json.loads(args[3])["inicio"] == NUEVO_INICIO.isoformat()
    sentencias = conn.sentencias()
    assert "COMMIT" not in sentencias[sentencias.index(t.SQL_MOVER) : posicion]


async def test_si_la_transaccion_del_cambio_hace_rollback_no_queda_aviso_encolado():
    app, conn, _, bot = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None)
    conn.fallas[t.SQL_MOVER] = pgx.ExclusionViolationError("conflicting key value violates exclusion constraint")
    await http(app).post(f"/turnos/{fila['id']}/mover", json={"start_at": NUEVO_INICIO.isoformat()}, headers=_h())
    assert not _outbox(conn, "enqueue_notification") and not bot.mensajes


async def test_si_falla_el_encolado_el_cambio_no_se_confirma():
    app, conn, _, bot = app_turnos()
    fila = _turno(conn)
    conn.fallas["enqueue_notification"] = RuntimeError("base caída")
    with pytest.raises(RuntimeError):
        await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    despues = conn.sentencias()[conn.sentencias().index(t.SQL_CAMBIAR_ESTADO) :]
    assert "ROLLBACK" in despues and "COMMIT" not in despues and not bot.mensajes


async def test_tras_el_envio_exitoso_la_fila_del_outbox_pasa_a_sent():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn)
    await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    ((_, enc),) = _outbox(conn, "enqueue_notification")
    cierres = [a for _, a in _outbox(conn, "complete_notification_outbox")]
    assert len(cierres) == 1 and cierres[0][1] == "sent" and cierres[0][0] is not None


async def test_si_telegram_agota_los_reintentos_la_fila_del_outbox_pasa_a_failed():
    app, conn, _, _ = app_turnos(bot=BotSimulado(estado=500))
    fila = _turno(conn)
    await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    cierres = [a for _, a in _outbox(conn, "complete_notification_outbox")]
    assert [c[1] for c in cierres] == ["failed"]


async def test_cliente_anonimizado_sin_canal_o_canal_no_soportado_no_encolan():
    for cambios in (
        {"customer_deleted_at": INICIO, "customer_channel": None, "customer_channel_user_id": None},
        {"customer_channel": None, "customer_channel_user_id": None},
        {"customer_channel": "whatsapp", "customer_channel_user_id": "549"},
    ):
        app, conn, _, _ = app_turnos()
        fila = _turno(conn, **cambios)
        await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
        assert not _outbox(conn, "enqueue_notification")


async def test_si_no_hay_integracion_de_bot_la_fila_se_cierra_como_failed():
    app, conn, _, _ = app_turnos(bot=_bot_sin_integracion())
    fila = _turno(conn)
    await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    cierres = [a for _, a in _outbox(conn, "complete_notification_outbox")]
    assert [c[1] for c in cierres] == ["failed"] and cierres[0][2] == "sin_integracion"


async def test_crear_o_completar_no_encolan_avisos():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None)
    await http(app).post(f"/turnos/{fila['id']}/completar", headers=_h())
    assert not _outbox(conn, "enqueue_notification")


async def test_el_token_del_bot_no_sale_en_la_respuesta():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn)
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert TOKEN_BOT not in r.text


# --- crear ---------------------------------------------------------------------------------------------------


async def test_crear_desde_el_dashboard_nace_confirmado_con_la_duracion_del_servicio_y_crea_el_evento():
    app, conn, calendario, bot = app_turnos()
    _alta_ok(conn, duration_minutes=45)
    cuerpo = _cuerpo_alta(tenant_id=str(uuid.uuid4()), status="pending")
    r = await http(app).post("/turnos", json=cuerpo, headers=_h())
    assert r.status_code == 201, r.text
    insertado = conn.turnos_insertados[0]
    assert insertado[0] == TENANT  # el del JWT; el del body se ignora
    assert insertado[4:6] == (INICIO, INICIO + timedelta(minutes=45))
    assert insertado[6] is None
    assert r.json()["status"] == "confirmed"
    assert r.json()["google_calendar_event_id"] == "evt-nuevo"
    assert _acciones(conn) == ["booking_created"]
    assert len(calendario.llamadas) == 1
    assert not bot.mensajes  # alta hecha por el negocio: no se avisa


@pytest.mark.parametrize(
    ("cambios", "estado", "codigo"),
    [
        ({"ofrece": False}, 422, "profesional_no_ofrece_servicio"),
        ({"service_active": False}, 422, "servicio_inactivo"),
        ({"professional_active": False}, 422, "profesional_inactivo"),
        ({"customer_deleted_at": INICIO}, 409, "cliente_anonimizado"),
    ],
)
async def test_crear_valida_servicio_profesional_y_cliente(cambios, estado, codigo):
    app, conn, calendario, _ = app_turnos()
    _alta_ok(conn, **cambios)
    r = await http(app).post("/turnos", json=_cuerpo_alta(), headers=_h())
    assert r.status_code == estado
    assert r.json()["detail"]["codigo"] == codigo
    assert not conn.turnos_insertados and not calendario.llamadas


async def test_crear_con_referencias_inexistentes_o_de_otro_tenant_es_422():
    app, conn, _, _ = app_turnos()
    conn.datos_alta = None
    r = await http(app).post("/turnos", json=_cuerpo_alta(), headers=_h())
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "referencia_invalida"


async def test_crear_con_fecha_sin_zona_horaria_es_422():
    app, conn, _, _ = app_turnos()
    _alta_ok(conn)
    r = await http(app).post("/turnos", json=_cuerpo_alta(start_at="2030-03-04T10:00:00"), headers=_h())
    assert r.status_code == 422
    assert not conn.turnos_insertados


async def test_crear_con_choque_de_horario_es_409_con_mensaje_claro_y_sin_efectos():
    app, conn, calendario, _ = app_turnos()
    _alta_ok(conn)
    conn.fallas[t.SQL_INSERTAR_TURNO] = pgx.ExclusionViolationError("conflicting key value violates exclusion")
    r = await http(app).post("/turnos", json=_cuerpo_alta(), headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "horario_ocupado"
    assert "superpone" in r.json()["detail"]["mensaje"]
    assert not calendario.llamadas and not conn.auditoria


async def test_crear_con_pack_consume_una_sesion_antes_de_insertar_y_guarda_el_pack_en_el_turno():
    app, conn, _, _ = app_turnos()
    _alta_ok(conn)
    pack = uuid.uuid4()
    r = await http(app).post("/turnos", json=_cuerpo_alta(booking_pack_id=str(pack)), headers=_h())
    assert r.status_code == 201, r.text
    sentencias = conn.sentencias()
    assert sentencias.index(t.SQL_CONSUMIR_PACK) < sentencias.index(t.SQL_INSERTAR_TURNO)
    assert conn.turnos_insertados[0][6] == pack
    args_pack = conn.ejecuciones(t.SQL_CONSUMIR_PACK)[0]
    assert args_pack[0] == TENANT and args_pack[1] == pack


async def test_crear_con_un_pack_sin_sesiones_ajeno_o_vencido_es_409_y_no_inserta():
    app, conn, calendario, _ = app_turnos()
    _alta_ok(conn)
    conn.pack_consumible = False
    r = await http(app).post("/turnos", json=_cuerpo_alta(booking_pack_id=str(uuid.uuid4())), headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "pack_no_disponible"
    assert not conn.turnos_insertados and not calendario.llamadas


async def test_crear_con_un_pack_comprado_sin_pagar_lo_rechaza_la_base_con_422():
    app, conn, _, _ = app_turnos()
    _alta_ok(conn)
    conn.fallas[t.SQL_INSERTAR_TURNO] = pgx.CheckViolationError("el pack no se puede redimir: es una compra")
    r = await http(app).post("/turnos", json=_cuerpo_alta(booking_pack_id=str(uuid.uuid4())), headers=_h())
    assert r.status_code == 422
    assert "ROLLBACK" in conn.sentencias()  # la sesión consumida vuelve con el rollback


# --- lectura -------------------------------------------------------------------------------------------------


async def test_listado_marca_los_turnos_de_clientes_anonimizados():
    app, conn, _, _ = app_turnos()
    anonimo, normal = uuid.uuid4(), uuid.uuid4()
    conn.eliminados = {anonimo: INICIO}
    conn.listado = [
        fila_turno(TENANT, status="confirmed", end_customer_id=anonimo, customer_name=None),
        fila_turno(TENANT, end_customer_id=normal),
    ]
    r = await http(app).get(
        "/turnos?desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z&estado=confirmed&estado=pending",
        headers=_h(escribe=False),
    )
    assert r.status_code == 200, r.text
    marcas = [x["sin_cliente_identificable"] for x in r.json()["turnos"]]
    assert marcas == [True, False]
    args = conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)[0]
    assert args[0] == TENANT and args[3] == ["confirmed", "pending"]


@pytest.mark.parametrize(
    "consulta",
    [
        "desde=2030-03-31T00:00:00Z&hasta=2030-03-01T00:00:00Z",
        "desde=2030-03-01T00:00:00&hasta=2030-03-31T00:00:00",
        "desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z&estado=inventado",
        "desde=2030-03-01T00:00:00Z",
        "desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z&limite=0",
    ],
)
async def test_listado_con_parametros_invalidos_es_422(consulta):
    app, conn, _, _ = app_turnos()
    r = await http(app).get(f"/turnos?{consulta}", headers=_h(escribe=False))
    assert r.status_code == 422
    assert not conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)


async def test_detalle_usa_turnos_queries_con_el_tenant_de_la_sesion_y_404_si_no_existe():
    app, conn, _, _ = app_turnos()
    turno_id = uuid.uuid4()
    r = await http(app).get(f"/turnos/{turno_id}", headers=_h(escribe=False))
    assert r.status_code == 404
    conn.detalle = fila_turno(TENANT, id=turno_id, status="confirmed", customer_name=None, customer_phone=None)
    conn.eliminados = {conn.detalle["end_customer_id"]: INICIO}
    r = await http(app).get(f"/turnos/{turno_id}", headers=_h(escribe=False))
    assert r.status_code == 200
    assert r.json()["sin_cliente_identificable"] is True
    assert conn.ejecuciones(q.SQL_DETALLE_BOOKING)[-1] == (TENANT, turno_id)


# --- packs ---------------------------------------------------------------------------------------------------


async def test_asignar_un_pack_manual_lo_marca_manual_grant_y_audita():
    app, conn, _, _ = app_turnos()
    cliente = uuid.uuid4()
    conn.cliente_pack = {"id": cliente, "deleted_at": None}
    servicio = uuid.uuid4()
    r = await http(app).post(
        "/packs",
        json={"end_customer_id": str(cliente), "service_id": str(servicio), "total_sessions": 5, "origin": "purchase"},
        headers=_h(),
    )
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["origin"] == "manual_grant" and cuerpo["payment_status"] == "not_required"
    assert cuerpo["used_sessions"] == 0 and cuerpo["total_sessions"] == 5
    assert conn.packs_insertados[0][:4] == (TENANT, cliente, servicio, 5)
    assert "manual_grant" in t.SQL_INSERTAR_PACK and "purchase" not in t.SQL_INSERTAR_PACK
    assert conn.auditoria[0][:2] == ("booking_pack_granted", "booking_packs")


@pytest.mark.parametrize("total", [0, -1, 1001])
async def test_un_pack_con_sesiones_fuera_de_rango_es_422(total):
    app, conn, _, _ = app_turnos()
    conn.cliente_pack = {"id": uuid.uuid4(), "deleted_at": None}
    r = await http(app).post(
        "/packs",
        json={"end_customer_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()), "total_sessions": total},
        headers=_h(),
    )
    assert r.status_code == 422
    assert not conn.packs_insertados


async def test_no_se_asigna_un_pack_a_un_cliente_anonimizado_ni_inexistente():
    app, conn, _, _ = app_turnos()
    cuerpo = {"end_customer_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()), "total_sessions": 3}
    conn.cliente_pack = None
    assert (await http(app).post("/packs", json=cuerpo, headers=_h())).status_code == 404
    conn.cliente_pack = {"id": uuid.uuid4(), "deleted_at": INICIO}
    r = await http(app).post("/packs", json=cuerpo, headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cliente_anonimizado"
    assert not conn.packs_insertados


async def test_un_pack_con_vencimiento_sin_zona_horaria_es_422():
    app, conn, _, _ = app_turnos()
    conn.cliente_pack = {"id": uuid.uuid4(), "deleted_at": None}
    r = await http(app).post(
        "/packs",
        json={
            "end_customer_id": str(uuid.uuid4()),
            "service_id": str(uuid.uuid4()),
            "total_sessions": 3,
            "expires_at": "2031-01-01T00:00:00",
        },
        headers=_h(),
    )
    assert r.status_code == 422


async def test_listar_packs_usa_turnos_queries_con_el_tenant_de_la_sesion():
    app, conn, _, _ = app_turnos()
    cliente = uuid.uuid4()
    conn.listado = [
        {
            "id": uuid.uuid4(),
            "tenant_id": TENANT,
            "end_customer_id": cliente,
            "service_id": uuid.uuid4(),
            "total_sessions": 5,
            "used_sessions": 1,
            "origin": "manual_grant",
            "payment_status": "not_required",
            "expires_at": None,
            "created_at": INICIO,
        }
    ]
    r = await http(app).get(f"/packs?end_customer_id={cliente}", headers=_h(escribe=False))
    assert r.status_code == 200
    assert r.json()["packs"][0]["used_sessions"] == 1
    assert "tenant_id" not in r.json()["packs"][0]
    args = conn.ejecuciones(q.SQL_LISTAR_PACKS)[0]
    assert args[0] == TENANT and args[1] == cliente


# --- lógica pura ---------------------------------------------------------------------------------------------


def test_el_texto_del_aviso_usa_la_hora_de_buenos_aires_y_no_lleva_datos_del_cliente():
    fila = fila_turno(TENANT)
    texto = t.texto_aviso("turno_confirmado", fila, INICIO)
    assert "04/03/2030" in texto and "10:00" in texto
    assert "Corte" in texto
    assert fila["customer_name"] not in texto and fila["customer_phone"] not in texto


def test_la_plantilla_desconocida_es_un_error_de_programacion():
    with pytest.raises(KeyError):
        t.texto_aviso("inventada", fila_turno(TENANT), INICIO)


def test_el_evento_de_calendar_de_un_cliente_anonimizado_no_tiene_datos_personales():
    fila = fila_turno(TENANT, customer_name=None, customer_phone=None, customer_deleted_at=INICIO)
    b = t.booking_para_calendario(fila, "confirmed", INICIO, INICIO + DURACION)
    assert b.cliente_nombre and not hasattr(b, "cliente_telefono")
    assert b.status == "confirmed" and b.inicio.tzinfo is not None


def test_las_sentencias_de_escritura_filtran_por_tenant_y_el_turno_se_bloquea_para_actualizar():
    assert "for update of b" in t.SQL_TURNO
    for sql in (t.SQL_TURNO, t.SQL_CAMBIAR_ESTADO, t.SQL_MOVER, t.SQL_GUARDAR_EVENTO, t.SQL_CONSUMIR_PACK):
        assert "tenant_id = $1" in sql
    assert "delete" not in "".join(
        s.lower() for s in (t.SQL_CAMBIAR_ESTADO, t.SQL_MOVER, t.SQL_INSERTAR_TURNO, t.SQL_INSERTAR_PACK)
    )


def test_inicio_de_prueba_es_futuro_respecto_de_hoy():
    assert INICIO > datetime.now(timezone.utc)  # noqa: UP017


# --- deadlock / serialization_failure: nunca 500 ------------------------------------------------------------


def _deadlock():
    return pgx.DeadlockDetectedError("deadlock detected")


async def test_crear_reintenta_si_la_base_detecta_un_deadlock_y_termina_en_201():
    app, conn, _, _ = app_turnos()
    _alta_ok(conn)
    conn.fallas_transitorias[t.SQL_INSERTAR_TURNO] = [_deadlock(), pgx.SerializationError("could not serialize")]
    r = await http(app).post("/turnos", json=_cuerpo_alta(), headers=_h())
    assert r.status_code == 201, r.text
    assert len(conn.turnos_insertados) == 1
    assert conn.sentencias().count("ROLLBACK") == 2


async def test_crear_con_deadlock_persistente_es_409_de_horario_ocupado_y_nunca_500():
    app, conn, calendario, _ = app_turnos()
    _alta_ok(conn)
    conn.fallas[t.SQL_INSERTAR_TURNO] = _deadlock()
    r = await http(app).post("/turnos", json=_cuerpo_alta(), headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "horario_ocupado"
    assert "deadlock" not in r.text
    assert not calendario.llamadas and not conn.auditoria


async def test_crear_serializa_por_profesional_con_un_advisory_lock_antes_de_tocar_pack_y_turno():
    app, conn, _, _ = app_turnos()
    _alta_ok(conn)
    pack = uuid.uuid4()
    r = await http(app).post("/turnos", json=_cuerpo_alta(booking_pack_id=str(pack)), headers=_h())
    assert r.status_code == 201, r.text
    sentencias = conn.sentencias()
    bloqueo = sentencias.index(t.SQL_BLOQUEAR_PROFESIONAL)
    assert bloqueo < sentencias.index(t.SQL_CONSUMIR_PACK) < sentencias.index(t.SQL_INSERTAR_TURNO)
    # asyncpg no convierte un UUID a text para `$n::text`: los argumentos del lock tienen que ser str.
    assert all(isinstance(a, str) for a in conn.ejecuciones(t.SQL_BLOQUEAR_PROFESIONAL)[0])


async def test_mover_reintenta_ante_un_deadlock_y_con_deadlock_persistente_es_409():
    app, conn, _, _ = app_turnos()
    fila = _turno(conn, status="confirmed", pending_expires_at=None)
    conn.fallas_transitorias[t.SQL_MOVER] = [_deadlock()]
    cuerpo = {"start_at": NUEVO_INICIO.isoformat()}
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    assert t.SQL_BLOQUEAR_PROFESIONAL in conn.sentencias()
    conn.fallas[t.SQL_MOVER] = _deadlock()
    r = await http(app).post(f"/turnos/{fila['id']}/mover", json=cuerpo, headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "horario_ocupado"


@pytest.mark.parametrize("ruta", ["/turnos?desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z&", "/packs?"])
async def test_offset_desmedido_es_422_y_uno_valido_es_200(ruta):
    app, conn, _, _ = app_turnos()
    r = await http(app).get(f"{ruta}offset=9223372036854775808", headers=_h(escribe=False))
    assert r.status_code == 422
    r = await http(app).get(f"{ruta}offset=10", headers=_h(escribe=False))
    assert r.status_code == 200, r.text
