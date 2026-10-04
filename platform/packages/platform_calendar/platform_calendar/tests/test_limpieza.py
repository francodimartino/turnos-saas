import uuid

import httpx

from platform_calendar import crear_calendario

PROF = uuid.uuid4()


async def _conectado(config, almacen, google):
    s = crear_calendario(config, almacen, google.cliente())
    integ = await almacen.guardar_profesional(PROF, None, '{"refresh_token": "' + google.refresh_emitido + '"}')
    return s, integ


async def test_limpiar_eventos_quita_nombre_y_telefono(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    fallidos = await s.limpiar_eventos([(integ, "e1"), (integ, "e2")])
    assert fallidos == []
    parches = [p for p in google.pedidos if p[0] == "PATCH"]
    assert len(parches) == 2 and parches[0][1].endswith("/calendars/primary/events/e1")
    for cuerpo in google.cuerpos_json("PATCH"):
        assert cuerpo["summary"] and cuerpo["description"]
        assert cuerpo["attendees"] == []
    # un solo refresco de token para los dos eventos de la misma conexión
    assert sum(1 for p in google.pedidos if "oauth2" in p[1]) == 1


async def test_limpiar_eventos_devuelve_fallidos_sin_lanzar(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    eliminada = uuid.uuid4()  # conexión que ya no existe
    google.eventos_status.update({"e404": 404, "e403": 403, "e500": 500})
    pares = [(integ, "ok"), (integ, "e404"), (integ, "e403"), (integ, "e500"), (eliminada, "eX")]
    fallidos = await s.limpiar_eventos(pares)
    por_evento = {f.event_id: f for f in fallidos}
    assert set(por_evento) == {"e404", "e403", "e500", "eX"}
    assert por_evento["e404"].motivo == "evento_no_encontrado"
    assert por_evento["e403"].motivo == "sin_permiso"
    assert por_evento["e500"].motivo == "error_api"
    assert por_evento["eX"].motivo == "conexion_eliminada"
    assert por_evento["eX"].integration_id == eliminada


async def test_limpiar_eventos_token_vencido(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    google.refresh_token_valido = "otro"  # Google rechaza el refresh_token guardado
    fallidos = await s.limpiar_eventos([(integ, "e1"), (integ, "e2")])
    assert [f.motivo for f in fallidos] == ["token_vencido", "token_vencido"]
    assert not any(p[0] == "PATCH" for p in google.pedidos)


async def test_fallidos_solo_llevan_ids_y_motivo(config, almacen, google):
    s, integ = await _conectado(config, almacen, google)
    google.eventos_status["e1"] = 403
    fallidos = await s.limpiar_eventos([(integ, "e1")])
    texto = repr(fallidos)
    for v in (google.access_emitido, google.refresh_emitido, config.client_secret):
        assert v not in texto
    assert set(fallidos[0]._fields) == {"integration_id", "event_id", "motivo"}


async def test_limpiar_eventos_error_de_red_no_lanza(config, almacen):
    def roto(request):
        raise httpx.ConnectError("sin red")

    s = crear_calendario(config, almacen, httpx.AsyncClient(transport=httpx.MockTransport(roto)))
    integ = await almacen.guardar_profesional(PROF, None, '{"refresh_token": "x"}')
    fallidos = await s.limpiar_eventos([(integ, "e1")])
    assert [f.motivo for f in fallidos] == ["error_red"]


async def test_limpiar_eventos_sin_pares(config, almacen, google):
    s = crear_calendario(config, almacen, google.cliente())
    assert await s.limpiar_eventos([]) == []
