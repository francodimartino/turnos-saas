import uuid
from datetime import datetime, timedelta

from platform_calendar import AccionSync, EventoCalendario, TextoNeutro, crear_calendario

PROF = uuid.uuid4()
INICIO = datetime.fromisoformat("2026-11-03T15:00:00+00:00")
FIN = INICIO + timedelta(minutes=45)


def _evento(id_externo=None):
    return EventoCalendario(
        titulo="Corte de pelo — Ana Pérez",
        descripcion="Servicio: Corte de pelo\nCliente: Ana Pérez",
        inicio=INICIO,
        fin=FIN,
        id_externo=id_externo,
    )


async def _conectado(config, almacen, google, con_integracion=True):
    s = crear_calendario(config, almacen, google.cliente())
    if con_integracion:
        await almacen.guardar_profesional(
            PROF, {"calendar_id": "primary"}, '{"refresh_token": "' + google.refresh_emitido + '"}'
        )
    return s


async def test_el_cuerpo_del_evento_enviado_a_google(config, almacen, google):
    s = await _conectado(config, almacen, google)
    await s.sincronizar_evento(PROF, _evento())
    assert google.cuerpos_json("POST")[-1] == {
        "summary": "Corte de pelo — Ana Pérez",
        "description": "Servicio: Corte de pelo\nCliente: Ana Pérez",
        "start": {"dateTime": INICIO.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        "end": {"dateTime": FIN.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
    }


async def test_sincronizar_evento_crea_y_despues_reescribe(config, almacen, google):
    s = await _conectado(config, almacen, google)
    creado = await s.sincronizar_evento(PROF, _evento())
    assert creado.accion is AccionSync.CREADO and creado.event_id == "evt1"
    actualizado = await s.sincronizar_evento(PROF, _evento("evt1"))
    assert actualizado.accion is AccionSync.ACTUALIZADO and actualizado.event_id == "evt1"
    assert google.pedidos[-1][0] == "PUT" and google.pedidos[-1][1].endswith("/events/evt1")


async def test_sincronizar_evento_sin_calendario_conectado_no_toca_nada(config, almacen, google):
    s = await _conectado(config, almacen, google, con_integracion=False)
    r = await s.sincronizar_evento(PROF, _evento("evtA"))
    assert r.accion is AccionSync.SIN_CONEXION and r.event_id == "evtA"
    assert google.pedidos == []


async def test_retirar_evento(config, almacen, google):
    s = await _conectado(config, almacen, google)
    assert (await s.retirar_evento(PROF, None)).accion is AccionSync.IGNORADO
    r = await s.retirar_evento(PROF, "evtA")
    assert r.accion is AccionSync.BORRADO and r.event_id is None
    assert google.pedidos[-1][0] == "DELETE"


async def test_retirar_evento_sin_calendario_conectado_conserva_el_id(config, almacen, google):
    s = await _conectado(config, almacen, google, con_integracion=False)
    r = await s.retirar_evento(PROF, "evtA")
    assert r.accion is AccionSync.SIN_CONEXION and r.event_id == "evtA"


async def test_anonimizar_eventos_escribe_el_texto_neutro_que_recibe(config, almacen, google):
    s = await _conectado(config, almacen, google)
    integ = await almacen.buscar("google_calendar", PROF)
    fallidos = await s.anonimizar_eventos([(integ, "e1")], TextoNeutro("Sin datos", "Borrado."))
    assert fallidos == []
    assert google.cuerpos_json("PATCH")[-1] == {"summary": "Sin datos", "description": "Borrado.", "attendees": []}
