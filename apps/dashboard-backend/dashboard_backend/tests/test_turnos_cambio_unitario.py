"""Cambio de horario pendiente desde el dashboard, sin base real: permisos, respuestas, avisos, Calendar y errores.

Qué hace la base con el horario (aplicar el propuesto, el choque, el vencimiento bajo lock, la carrera entre dos
aprobaciones y el aislamiento por tenant con el rol real) se prueba contra Postgres en
`test_turnos_cambio_integracion.py`. Acá la conexión es falsa y Telegram y Calendar van simulados.
"""

import json
import uuid
from datetime import timedelta

import pytest
from turnos_dominio import cambios as dominio
from turnos_queries import consultas as q

from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_turnos import (
    CHAT_ID,
    DURACION,
    INICIO,
    app_turnos,
    ficha_de_canales,
    fila_turno,
)

TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
PROPUESTO = INICIO + timedelta(days=1)


def _h(escribe=True, user=STAFF):
    h = {"Cookie": "dash_acceso=" + token(user_id=user, tenant_id=TENANT)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _con_cambio(conn, **cambios):
    """Un turno `confirmed` con un cambio de horario pendiente, como lo deja un pedido del cliente."""
    base = {
        "status": "confirmed",
        "pending_expires_at": None,
        "starts_at_propuesto": PROPUESTO,
        "ends_at_propuesto": PROPUESTO + DURACION,
        "cambio_pendiente_expira_at": INICIO - timedelta(hours=2),
    }
    base.update(cambios)
    fila = fila_turno(TENANT, **base)
    conn.turnos[fila["id"]] = fila
    return fila


def _encolados(conn):
    return [a for s, a in conn.ejecutado if "enqueue_notification" in s]


def _acciones(conn):
    return [a[0] for a in conn.auditoria]


RUTAS = ("aprobar", "rechazar")


# --- permisos -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("accion", RUTAS)
async def test_sin_approve_bookings_es_403_y_no_toca_nada(accion):
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn)
    conn.estado_staff["tiene"] = False
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert fila["starts_at_propuesto"] == PROPUESTO and fila["start_at"] == INICIO
    assert not conn.auditoria and not _encolados(conn) and not calendario.llamadas and not bot.mensajes
    assert not conn.sentencias_que_contienen("update public.booking")


@pytest.mark.parametrize("accion", RUTAS)
async def test_con_exactamente_approve_bookings_alcanza_aunque_no_sea_owner(accion):
    app, conn, _, _ = app_turnos()
    fila = _con_cambio(conn)
    conn.estado_staff.update({"is_owner": False, "tiene": True})
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 200, r.text
    permisos = [args[2] for sql, args in conn.ejecutado if "tenant_user_permissions" in sql]
    assert permisos[0] == "approve_bookings"


@pytest.mark.parametrize("accion", RUTAS)
async def test_el_profesional_vinculado_sin_el_permiso_no_resuelve_un_cambio(accion):
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    fila = _con_cambio(conn, professional_auth_user_id=STAFF)
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 403
    assert fila["starts_at_propuesto"] == PROPUESTO


@pytest.mark.parametrize("accion", RUTAS)
async def test_sin_sesion_es_401(accion):
    app, _, _, _ = app_turnos()
    r = await http(app).post(f"/turnos/{uuid.uuid4()}/cambio/{accion}", headers={"Origin": ORIGEN})
    assert r.status_code == 401


# --- aprobar --------------------------------------------------------------------------------------------------


async def test_aprobar_mueve_al_horario_nuevo_audita_sincroniza_calendar_y_avisa():
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn, google_calendar_event_id="evt-1")
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "confirmed"
    assert cuerpo["start_at"] == PROPUESTO.isoformat()
    assert cuerpo["starts_at_propuesto"] is None and cuerpo["cambio_pendiente_expira_at"] is None
    assert cuerpo["calendario"] == {"estado": "actualizado"}
    assert cuerpo["aviso"] == "programado"
    assert "tenant_id" not in cuerpo
    assert conn.auditoria[0][:3] == ("booking_change_approved", "bookings", fila["id"])
    assert json.loads(conn.auditoria[0][3])["canal"] == "staff"
    assert len(calendario.llamadas) == 1
    assert calendario.llamadas[0].inicio == PROPUESTO and calendario.llamadas[0].google_calendar_event_id == "evt-1"
    (cliente_aviso,) = _encolados(conn)
    assert cliente_aviso[0] == fila["end_customer_id"] and cliente_aviso[2] == "cambio_horario_aprobado"
    assert json.loads(cliente_aviso[3])["inicio"] == PROPUESTO.isoformat()
    assert len(bot.mensajes) == 1 and bot.mensajes[0]["chat_id"] == CHAT_ID
    assert "aprobado" in bot.mensajes[0]["text"] and "Corte" in bot.mensajes[0]["text"]


async def test_aprobar_encola_el_aviso_dentro_de_la_transaccion_y_antes_del_commit():
    app, conn, calendario, _ = app_turnos()
    fila = _con_cambio(conn)
    await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    sentencias = conn.sentencias()
    aplicar = sentencias.index(dominio.alta.SQL_APLICAR_CAMBIO_PENDIENTE)
    aviso = next(i for i, s in enumerate(sentencias) if "enqueue_notification" in s)
    assert aplicar < aviso < sentencias.index("COMMIT", aviso)
    assert "COMMIT" not in sentencias[aplicar:aviso]
    assert calendario.sentencias_al_sincronizar.count("COMMIT") >= 1  # Calendar se toca recién tras el COMMIT


async def test_el_aviso_no_va_por_telegram_si_la_ficha_tiene_otro_canal_y_queda_pendiente_de_envio():
    app, conn, _, bot = app_turnos()
    fila = _con_cambio(conn)
    conn.fichas[fila["end_customer_id"]] = ficha_de_canales([("telegram", CHAT_ID), ("email", "a@prueba.invalid")])
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "pendiente_de_envio"
    assert len(_encolados(conn)) == 1 and not bot.mensajes


async def test_un_cliente_anonimizado_no_recibe_aviso_pero_el_cambio_se_resuelve():
    app, conn, _, bot = app_turnos()
    fila = _con_cambio(conn, customer_deleted_at=INICIO)
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["aviso"] == "cliente_anonimizado" and r.json()["sin_cliente_identificable"] is True
    assert not _encolados(conn) and not bot.mensajes


# --- rechazar -------------------------------------------------------------------------------------------------


async def test_rechazar_conserva_el_horario_original_y_avisa_con_ese_horario():
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn, google_calendar_event_id="evt-1")
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/rechazar", headers=_h())
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "confirmed"
    assert cuerpo["start_at"] == INICIO.isoformat()
    assert cuerpo["starts_at_propuesto"] is None and cuerpo["ends_at_propuesto"] is None
    assert cuerpo["aviso"] == "programado"
    assert calendario.llamadas[0].inicio == INICIO
    (args,) = _encolados(conn)
    assert args[2] == "cambio_horario_rechazado" and json.loads(args[3])["inicio"] == INICIO.isoformat()
    assert "no aprobó" in bot.mensajes[0]["text"]
    assert conn.auditoria[0][:3] == ("booking_change_rejected", "bookings", fila["id"])


async def test_rechazar_sirve_aunque_el_cambio_ya_haya_vencido():
    app, conn, _, _ = app_turnos()
    fila = _con_cambio(conn, cambio_pendiente_expira_at=INICIO - timedelta(days=4000))
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/rechazar", headers=_h())
    assert r.status_code == 200, r.text
    assert fila["starts_at_propuesto"] is None and fila["start_at"] == INICIO


# --- errores: ninguno es 500 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("accion", RUTAS)
async def test_un_turno_inexistente_o_de_otro_tenant_es_404_sin_efectos(accion):
    app, conn, calendario, bot = app_turnos()
    ajeno = fila_turno(
        uuid.uuid4(), status="confirmed", pending_expires_at=None, starts_at_propuesto=PROPUESTO,
        ends_at_propuesto=PROPUESTO + DURACION, cambio_pendiente_expira_at=INICIO - timedelta(hours=1),
    )  # fmt: skip
    conn.turnos[ajeno["id"]] = ajeno
    for turno_id in (uuid.uuid4(), ajeno["id"]):
        r = await http(app).post(f"/turnos/{turno_id}/cambio/{accion}", headers=_h())
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["codigo"] == "turno_no_encontrado"
    assert ajeno["starts_at_propuesto"] == PROPUESTO and ajeno["start_at"] == INICIO
    assert not conn.auditoria and not _encolados(conn) and not calendario.llamadas and not bot.mensajes


@pytest.mark.parametrize("accion", RUTAS)
@pytest.mark.parametrize("estado", ["pending", "cancelled", "completed"])
async def test_un_turno_que_no_esta_confirmado_no_tiene_cambio_pendiente_es_409(accion, estado):
    app, conn, _, _ = app_turnos()
    vencimiento = INICIO - timedelta(hours=1) if estado == "pending" else None
    fila = _con_cambio(
        conn,
        status=estado,
        starts_at_propuesto=None,
        ends_at_propuesto=None,
        cambio_pendiente_expira_at=None,
        pending_expires_at=vencimiento,
    )
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cambio_no_pendiente"


@pytest.mark.parametrize("accion", RUTAS)
async def test_un_turno_confirmado_sin_cambio_pendiente_es_409_sin_auditar(accion):
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn, starts_at_propuesto=None, ends_at_propuesto=None, cambio_pendiente_expira_at=None)
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_no_pendiente"
    assert not conn.auditoria and not _encolados(conn) and not calendario.llamadas and not bot.mensajes


async def test_resolver_dos_veces_el_mismo_cambio_la_segunda_es_409():
    app, conn, _, _ = app_turnos()
    fila = _con_cambio(conn)
    assert (await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())).status_code == 200
    for accion in RUTAS:
        r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
        assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_no_pendiente"
    assert len(_encolados(conn)) == 1


async def test_aprobar_un_cambio_vencido_es_409_y_no_mueve_nada():
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn, cambio_pendiente_expira_at=INICIO - timedelta(days=4000))
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cambio_vencido"
    assert fila["start_at"] == INICIO and fila["starts_at_propuesto"] == PROPUESTO
    assert not conn.auditoria and not _encolados(conn) and not calendario.llamadas and not bot.mensajes


async def test_aprobar_con_el_horario_propuesto_ya_pasado_es_409_cambio_vencido():
    app, conn, _, _ = app_turnos()
    pasado = INICIO - timedelta(days=4000)
    fila = _con_cambio(conn, starts_at_propuesto=pasado, ends_at_propuesto=pasado + DURACION)
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_vencido"


async def test_aprobar_con_el_horario_original_ya_pasado_es_409_cambio_vencido():
    app, conn, _, _ = app_turnos()
    pasado = INICIO - timedelta(days=4000)
    fila = _con_cambio(
        conn, start_at=pasado, end_at=pasado + DURACION, cambio_pendiente_expira_at=INICIO - timedelta(hours=1)
    )
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "cambio_vencido"
    assert fila["start_at"] == pasado


class _ErrorDeBase(Exception):
    """Como los errores de asyncpg para el dominio: lo que cuenta es el `sqlstate`."""

    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


async def test_un_horario_nuevo_ocupado_al_aprobar_es_409_horario_ocupado_y_nada_cambia():
    app, conn, calendario, bot = app_turnos()
    fila = _con_cambio(conn)
    conn.fallas[dominio.alta.SQL_APLICAR_CAMBIO_PENDIENTE] = _ErrorDeBase("23P01")
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "horario_ocupado"
    assert fila["start_at"] == INICIO and fila["starts_at_propuesto"] == PROPUESTO
    assert not conn.auditoria and not _encolados(conn) and not calendario.llamadas and not bot.mensajes
    assert "ROLLBACK" in conn.sentencias()


@pytest.mark.parametrize(
    ("accion", "sql"),
    [
        ("aprobar", dominio.alta.SQL_APLICAR_CAMBIO_PENDIENTE),
        ("rechazar", dominio.SQL_DESCARTAR_CAMBIO),
    ],
)
async def test_una_carrera_que_persiste_tras_reintentar_es_409_y_no_500(accion, sql):
    app, conn, _, _ = app_turnos()
    fila = _con_cambio(conn)
    conn.fallas_transitorias[sql] = [_ErrorDeBase("40P01"), _ErrorDeBase("40001")]
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/{accion}", headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "operacion_concurrente"
    assert not conn.auditoria and fila["starts_at_propuesto"] == PROPUESTO


async def test_una_carrera_que_se_resuelve_al_reintentar_termina_bien():
    app, conn, _, _ = app_turnos()
    fila = _con_cambio(conn)
    conn.fallas_transitorias[dominio.alta.SQL_APLICAR_CAMBIO_PENDIENTE] = [_ErrorDeBase("40P01")]
    r = await http(app).post(f"/turnos/{fila['id']}/cambio/aprobar", headers=_h())
    assert r.status_code == 200, r.text
    assert fila["start_at"] == PROPUESTO


@pytest.mark.parametrize("accion", RUTAS)
async def test_un_id_que_no_es_uuid_es_422(accion):
    app, conn, _, _ = app_turnos()
    r = await http(app).post(f"/turnos/no-es-un-id/cambio/{accion}", headers=_h())
    assert r.status_code == 422
    assert not conn.sentencias_que_contienen("update public.booking")


# --- listado con cambio pendiente -----------------------------------------------------------------------------


async def test_listar_con_cambio_pendiente_no_pide_rango_y_filtra_en_la_consulta():
    app, conn, _, _ = app_turnos()
    conn.listado = [fila_turno(TENANT, status="confirmed", starts_at_propuesto=PROPUESTO,
                               ends_at_propuesto=PROPUESTO + DURACION, cambio_pendiente_expira_at=INICIO)]  # fmt: skip
    r = await http(app).get("/turnos?cambio_pendiente=true", headers=_h(escribe=False))
    assert r.status_code == 200, r.text
    (turno,) = r.json()["turnos"]
    assert turno["starts_at_propuesto"] == PROPUESTO.isoformat()
    assert turno["cambio_pendiente_expira_at"] is not None
    args = conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)[0]
    assert args[0] == TENANT and args[1] is None and args[2] is None and args[7] is True


async def test_listar_sin_cambio_pendiente_sigue_exigiendo_el_rango_y_no_filtra():
    app, conn, _, _ = app_turnos()
    r = await http(app).get("/turnos", headers=_h(escribe=False))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "rango_requerido"
    r = await http(app).get("/turnos?cambio_pendiente=false", headers=_h(escribe=False))
    assert r.status_code == 422
    r = await http(app).get(
        "/turnos?desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z&cambio_pendiente=false",
        headers=_h(escribe=False),
    )
    assert r.status_code == 200
    assert conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)[-1][7] is False


async def test_listar_con_cambio_pendiente_y_rango_respeta_ambos_y_un_cambio_pendiente_invalido_es_422():
    app, conn, _, _ = app_turnos()
    r = await http(app).get(
        "/turnos?cambio_pendiente=true&desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z", headers=_h(escribe=False)
    )
    assert r.status_code == 200
    args = conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)[0]
    assert args[1] is not None and args[2] is not None and args[7] is True
    for consulta in ("cambio_pendiente=quizas", "cambio_pendiente=true&desde=2030-03-01T00:00:00Z",
                     "cambio_pendiente=true&desde=2030-03-31T00:00:00Z&hasta=2030-03-01T00:00:00Z"):  # fmt: skip
        assert (await http(app).get(f"/turnos?{consulta}", headers=_h(escribe=False))).status_code == 422


async def test_listar_cambio_pendiente_exige_view_bookings():
    app, conn, _, _ = app_turnos()
    conn.estado_staff["tiene"] = False
    r = await http(app).get("/turnos?cambio_pendiente=true", headers=_h(escribe=False))
    assert r.status_code == 403 and not conn.ejecuciones(q.SQL_LISTAR_BOOKINGS)
