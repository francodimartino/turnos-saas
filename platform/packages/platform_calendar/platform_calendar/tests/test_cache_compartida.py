"""Caché de tokens de acceso compartida entre servicios (un servicio por operación, una caché por proceso)."""

import uuid

from platform_calendar import crear_calendario

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
    try:
        await s.borrar_evento(integ, "e1")
    except Exception:
        pass
    assert integ not in cache
