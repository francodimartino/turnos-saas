import re
import uuid
from datetime import datetime, timedelta

import pytest

from platform_calendar import (
    AccionSync,
    BookingCalendario,
    ErrorCalendar,
    ErrorOAuth,
    crear_calendario,
)

PROF = uuid.uuid4()
INICIO = datetime.fromisoformat("2026-11-03T15:00:00+00:00")


def _booking(status="confirmed", event_id=None, **kw):
    datos = dict(
        id=uuid.uuid4(),
        status=status,
        professional_id=PROF,
        servicio="Corte de pelo",
        cliente_nombre="Ana Pérez",
        inicio=INICIO,
        fin=INICIO + timedelta(minutes=45),
        google_calendar_event_id=event_id,
    )
    datos.update(kw)
    return BookingCalendario(**datos)


async def _conectado(config, almacen, google):
    s = crear_calendario(config, almacen, google.cliente())
    integ = await almacen.guardar_profesional(
        PROF, {"calendar_id": "primary"}, '{"refresh_token": "' + google.refresh_emitido + '"}'
    )
    return s, integ


async def test_crear_evento_con_servicio_y_nombre_sin_telefono(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    event_id = await s.crear_evento(integ, _booking())
    assert event_id == "evt1"
    metodo, url, auth, _ = google.pedidos[-1]
    assert metodo == "POST" and url.endswith("/calendars/primary/events")
    assert auth == f"Bearer {google.access_emitido}"
    cuerpo = google.cuerpos_json("POST")[-1]
    assert "Corte de pelo" in cuerpo["summary"] and "Ana Pérez" in cuerpo["summary"]
    assert cuerpo["description"] == "Servicio: Corte de pelo\nCliente: Ana Pérez"
    assert not re.search(r"\d{6,}|tel", str(cuerpo), re.IGNORECASE)
    assert cuerpo["start"]["dateTime"] == INICIO.isoformat()
    assert cuerpo["end"]["dateTime"] == (INICIO + timedelta(minutes=45)).isoformat()


async def test_actualizar_evento_mueve_horario(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    nuevo = INICIO + timedelta(days=1)
    await s.actualizar_evento(integ, "evtX", _booking(inicio=nuevo, fin=nuevo + timedelta(minutes=45)))
    metodo, url, _, _ = google.pedidos[-1]
    assert metodo == "PUT" and url.endswith("/calendars/primary/events/evtX")
    assert google.cuerpos_json("PUT")[-1]["start"]["dateTime"] == nuevo.isoformat()


async def test_actualizar_evento_reescribe_la_descripcion_sin_telefono(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    await s.actualizar_evento(integ, "evtViejo", _booking())
    cuerpo = google.cuerpos_json("PUT")[-1]
    assert "Teléfono" not in cuerpo["description"] and "+549" not in str(cuerpo)
    assert "Ana Pérez" in cuerpo["summary"]


async def test_borrar_evento_y_404_410_son_idempotentes(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    await s.borrar_evento(integ, "evtX")
    assert google.pedidos[-1][0] == "DELETE"
    google.eventos_status["yaborrado"] = 404
    await s.borrar_evento(integ, "yaborrado")
    google.eventos_status["gone"] = 410
    await s.borrar_evento(integ, "gone")


async def test_error_de_api_levanta_error_sin_tokens(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    google.eventos_status["malo"] = 500
    with pytest.raises(ErrorCalendar) as e:
        await s.actualizar_evento(integ, "malo", _booking())
    assert "500" in str(e.value)
    for v in (google.access_emitido, google.refresh_emitido, config.client_secret):
        assert v not in str(e.value) and v not in repr(e.value)


async def test_refresh_token_revocado_levanta_error_oauth_sin_tokens(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    google.refresh_token_valido = "otro"
    with pytest.raises(ErrorOAuth) as e:
        await s.crear_evento(integ, _booking())
    assert google.refresh_emitido not in str(e.value)


async def test_sync_pending_se_ignora_sin_tocar_google(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    r = await s.sincronizar_booking(_booking("pending"))
    assert r.accion == AccionSync.IGNORADO and r.event_id is None
    assert google.pedidos == []


async def test_sync_confirmed_sin_evento_crea(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    r = await s.sincronizar_booking(_booking("confirmed"))
    assert r.accion == AccionSync.CREADO and r.event_id == "evt1"


async def test_sync_confirmed_con_evento_actualiza(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    r = await s.sincronizar_booking(_booking("confirmed", event_id="evtA"))
    assert r.accion == AccionSync.ACTUALIZADO and r.event_id == "evtA"
    assert google.pedidos[-1][0] == "PUT"


async def test_sync_cancelled_con_evento_borra_y_sin_evento_ignora(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    r = await s.sincronizar_booking(_booking("cancelled", event_id="evtA"))
    assert r.accion == AccionSync.BORRADO and r.event_id is None
    assert google.pedidos[-1][0] == "DELETE"
    n = len(google.pedidos)
    r = await s.sincronizar_booking(_booking("cancelled"))
    assert r.accion == AccionSync.IGNORADO and len(google.pedidos) == n


async def test_sync_completed_no_toca_el_calendario(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    r = await s.sincronizar_booking(_booking("completed", event_id="evtA"))
    assert r.accion == AccionSync.IGNORADO and r.event_id == "evtA"
    assert google.pedidos == []


async def test_sync_profesional_sin_conexion(config, almacen, google):
    s = crear_calendario(config, almacen, google.cliente())
    r = await s.sincronizar_booking(_booking("confirmed"))
    assert r.accion == AccionSync.SIN_CONEXION
    assert google.pedidos == []


async def test_sync_estado_desconocido_falla(config, almacen, google):
    s, _ = await _conectado(config, almacen, google)
    with pytest.raises(ErrorCalendar):
        await s.sincronizar_booking(_booking("rarisimo"))
