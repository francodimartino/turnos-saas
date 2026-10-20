import uuid
from datetime import datetime, timedelta

import pytest

from platform_calendar import (
    ErrorCalendar,
    ErrorOAuth,
    EventoCalendario,
    crear_calendario,
)

PROF = uuid.uuid4()
INICIO = datetime.fromisoformat("2026-11-03T15:00:00+00:00")


def _evento(id_externo=None, inicio=INICIO, fin=None):
    return EventoCalendario(
        titulo="Corte de pelo — Ana Pérez",
        descripcion="Servicio: Corte de pelo\nCliente: Ana Pérez",
        inicio=inicio,
        fin=fin or inicio + timedelta(minutes=45),
        id_externo=id_externo,
    )


async def _conectado(config, almacen, google):
    s = crear_calendario(config, almacen, google.cliente())
    integ = await almacen.guardar_profesional(
        PROF, {"calendar_id": "primary"}, '{"refresh_token": "' + google.refresh_emitido + '"}'
    )
    return s, integ


async def test_crear_evento_con_el_titulo_y_la_descripcion_recibidos(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    r = await s.sincronizar_evento(PROF, _evento())
    assert r.event_id == "evt1"
    metodo, url, auth, _ = google.pedidos[-1]
    assert metodo == "POST" and url.endswith("/calendars/primary/events")
    assert auth == f"Bearer {google.access_emitido}"
    cuerpo = google.cuerpos_json("POST")[-1]
    assert cuerpo["summary"] == "Corte de pelo — Ana Pérez"
    assert cuerpo["description"] == "Servicio: Corte de pelo\nCliente: Ana Pérez"
    assert set(cuerpo) == {"summary", "description", "start", "end"}
    assert cuerpo["start"]["dateTime"] == INICIO.isoformat()
    assert cuerpo["end"]["dateTime"] == (INICIO + timedelta(minutes=45)).isoformat()


async def test_actualizar_evento_mueve_horario(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    nuevo = INICIO + timedelta(days=1)
    await s.sincronizar_evento(PROF, _evento("evtX", inicio=nuevo))
    metodo, url, _, _ = google.pedidos[-1]
    assert metodo == "PUT" and url.endswith("/calendars/primary/events/evtX")
    assert google.cuerpos_json("PUT")[-1]["start"]["dateTime"] == nuevo.isoformat()


async def test_actualizar_evento_reescribe_titulo_y_descripcion(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    await s.sincronizar_evento(PROF, _evento("evtViejo"))
    cuerpo = google.cuerpos_json("PUT")[-1]
    assert cuerpo["description"] == "Servicio: Corte de pelo\nCliente: Ana Pérez"
    assert cuerpo["summary"] == "Corte de pelo — Ana Pérez"


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
        await s.sincronizar_evento(PROF, _evento("malo"))
    assert "500" in str(e.value)
    for v in (google.access_emitido, google.refresh_emitido, config.client_secret):
        assert v not in str(e.value) and v not in repr(e.value)


async def test_refresh_token_revocado_levanta_error_oauth_sin_tokens(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    google.refresh_token_valido = "otro"
    with pytest.raises(ErrorOAuth) as e:
        await s.sincronizar_evento(PROF, _evento())
    assert google.refresh_emitido not in str(e.value)
