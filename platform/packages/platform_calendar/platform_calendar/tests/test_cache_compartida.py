"""Caché de tokens de acceso compartida entre servicios (un servicio por operación, una caché por proceso)."""

import contextlib
import uuid

import pytest

from platform_calendar import ErrorConexionEliminada, ErrorTokenVencido, crear_calendario

PROF = uuid.uuid4()


def _pedidos_de_token(google):
    return [p for p in google.pedidos if "oauth2.googleapis.com" in p[1]]


async def _integracion(almacen, google):
    return await almacen.guardar_profesional(
        PROF, {"calendar_id": "primary"}, '{"refresh_token": "' + google.refresh_emitido + '"}'
    )


async def test_dos_servicios_con_la_misma_cache_piden_un_solo_token(config, almacen, google):
    integ = await _integracion(almacen, google)
    cache: dict = {}
    for _ in range(2):
        s = crear_calendario(config, almacen, google.cliente(), cache_tokens=cache)
        await s.token_de_acceso(integ)
    assert len(_pedidos_de_token(google)) == 1


async def test_sin_cache_compartida_cada_servicio_pide_el_suyo(config, almacen, google):
    integ = await _integracion(almacen, google)
    for _ in range(2):
        await crear_calendario(config, almacen, google.cliente()).token_de_acceso(integ)
    assert len(_pedidos_de_token(google)) == 2


async def test_token_vencido_o_dentro_del_margen_se_vuelve_a_pedir(config, almacen, google):
    integ = await _integracion(almacen, google)
    ahora = [1000.0]
    cache: dict = {}

    def servicio():
        return crear_calendario(config, almacen, google.cliente(), reloj=lambda: ahora[0], cache_tokens=cache)

    await servicio().token_de_acceso(integ)  # expires_in 3600, margen 60: vale hasta 4540
    ahora[0] = 4000.0
    await servicio().token_de_acceso(integ)
    assert len(_pedidos_de_token(google)) == 1
    ahora[0] = 4600.0  # dentro del margen / vencido
    await servicio().token_de_acceso(integ)
    assert len(_pedidos_de_token(google)) == 2


async def test_un_401_de_la_api_descarta_el_token_cacheado(config, almacen, google):
    integ = await _integracion(almacen, google)
    cache: dict = {}
    s = crear_calendario(config, almacen, google.cliente(), cache_tokens=cache)
    google.eventos_status["e1"] = 401
    with contextlib.suppress(Exception):
        await s.borrar_evento(integ, "e1")
    assert integ not in cache


async def test_descartar_token_obliga_a_pedir_otro_y_no_toca_a_las_demas_integraciones(config, almacen, google):
    integ = await _integracion(almacen, google)
    otra = await almacen.guardar_profesional(
        uuid.uuid4(), {"calendar_id": "primary"}, '{"refresh_token": "' + google.refresh_emitido + '"}'
    )
    cache: dict = {}
    s = crear_calendario(config, almacen, google.cliente(), reloj=lambda: 1000.0, cache_tokens=cache)
    await s.token_de_acceso(integ)
    await s.token_de_acceso(otra)
    assert len(_pedidos_de_token(google)) == 2
    s.descartar_token(integ)
    assert integ not in cache and otra in cache
    await s.token_de_acceso(otra)  # sigue cacheado
    assert len(_pedidos_de_token(google)) == 2
    await s.token_de_acceso(integ)  # el descartado vuelve a pedir
    assert len(_pedidos_de_token(google)) == 3


async def test_tras_desconectar_no_se_usa_el_token_viejo_contra_google(config, almacen, google):
    integ = await _integracion(almacen, google)
    cache: dict = {}
    s = crear_calendario(config, almacen, google.cliente(), reloj=lambda: 1000.0, cache_tokens=cache)
    await s.token_de_acceso(integ)
    await almacen.eliminar_profesional(PROF)
    s.descartar_token(integ)
    n = len(google.pedidos)
    with pytest.raises(ErrorConexionEliminada):
        await s.borrar_evento(integ, "e1")
    assert len(google.pedidos) == n  # ningún pedido a Google, ni con el token viejo


async def test_invalid_grant_al_refrescar_descarta_el_token_cacheado(config, almacen, google):
    integ = await _integracion(almacen, google)
    cache: dict = {}
    ahora = [1000.0]
    s = crear_calendario(config, almacen, google.cliente(), reloj=lambda: ahora[0], cache_tokens=cache)
    await s.token_de_acceso(integ)
    ahora[0] = 5000.0  # vencido: se refresca y Google revoca
    google.refresh_token_valido = "otro"
    with pytest.raises(ErrorTokenVencido):
        await s.token_de_acceso(integ)
    assert integ not in cache
