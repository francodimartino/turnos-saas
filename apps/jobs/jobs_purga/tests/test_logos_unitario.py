"""Borrado del logo en la purga: cliente real de Storage sobre un transporte HTTP falso, sin red ni base."""

import json
import logging
import uuid

import httpx
import pytest
from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    AlmacenamientoSinRespuesta,
    ConfiguracionAlmacenamientoInvalida,
    ErrorAlmacenamiento,
)

from jobs_purga import logos
from jobs_purga.logos import (
    BUCKET_LOGOS,
    TIMEOUT_STORAGE_SEG,
    BorradorLogos,
    borrador_desde_entorno,
)

TENANT = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTRO = uuid.UUID("99999999-8888-7777-6666-555555555555")
RUTA = f"{TENANT}/logo-{'0123456789abcdef' * 2}.png"
URL = "https://proy.supabase.co"
# Generada en tiempo de ejecución: ningún literal con forma de secreto en el repo.
CLAVE = f"clave-{uuid.uuid4().hex}"


def _borrador(handler, **kw) -> BorradorLogos:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return borrador_desde_entorno({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": CLAVE}, http=http, **kw)


def _respuesta(peticiones, estado=200, cuerpo=None):
    def handler(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        return httpx.Response(estado, json=[] if cuerpo is None else cuerpo)

    return handler


async def test_borra_la_ruta_del_tenant_en_el_bucket_de_logos_con_la_clave():
    reqs: list[httpx.Request] = []
    await _borrador(_respuesta(reqs))(TENANT, RUTA)
    (req,) = reqs
    assert req.method == "DELETE" and req.url.path == f"/storage/v1/object/{BUCKET_LOGOS}"
    assert json.loads(req.content) == {"prefixes": [RUTA]}
    assert req.headers["authorization"] == f"Bearer {CLAVE}"


async def test_un_404_es_exito_porque_el_logo_ya_estaba_borrado():
    reqs: list[httpx.Request] = []
    await _borrador(_respuesta(reqs, 404, {"message": "no existe"}))(TENANT, RUTA)  # no lanza
    assert len(reqs) == 1


@pytest.mark.parametrize("estado", [400, 401, 403, 500, 502, 503])
async def test_un_error_de_storage_lanza_para_que_la_purga_reintente(estado):
    with pytest.raises(AlmacenamientoNoDisponible):
        await _borrador(_respuesta([], estado))(TENANT, RUTA)


@pytest.mark.parametrize("falla", [httpx.ReadTimeout("lento"), httpx.ConnectError("sin red")])
async def test_un_timeout_o_corte_de_red_lanza_sin_respuesta(falla):
    def handler(req):
        raise falla

    with pytest.raises(AlmacenamientoSinRespuesta):
        await _borrador(handler)(TENANT, RUTA)


@pytest.mark.parametrize(
    "ruta",
    [
        f"{OTRO}/logo-{'0123456789abcdef' * 2}.png",  # carpeta de otro tenant
        f"{TENANT}/../{OTRO}/logo-{'0123456789abcdef' * 2}.png",  # sube de carpeta
        f"{TENANT}/otra-cosa.png",  # no es un logo
        f"{TENANT}/sub/logo-{'0123456789abcdef' * 2}.png",  # subcarpeta
        f"{TENANT}/",  # carpeta entera
        f"logos/{TENANT}/logo-{'0123456789abcdef' * 2}.png",  # con el bucket adelante
        f"https://otro.example/{TENANT}/logo-{'0123456789abcdef' * 2}.png",
        f"{TENANT}/logo-{'0123456789abcdef' * 2}.svg",
        "",
    ],
)
async def test_una_ruta_con_forma_inesperada_no_pide_nada_a_storage(ruta):
    reqs: list[httpx.Request] = []
    with pytest.raises(ErrorAlmacenamiento):
        await _borrador(_respuesta(reqs))(TENANT, ruta)
    assert reqs == []


async def test_el_logo_de_un_tenant_no_se_borra_pidiendo_la_ruta_de_otro():
    reqs: list[httpx.Request] = []
    borrador = _borrador(_respuesta(reqs))
    with pytest.raises(ErrorAlmacenamiento):
        await borrador(OTRO, RUTA)  # la ruta es del tenant TENANT
    assert reqs == []


@pytest.mark.parametrize("clave", [None, "", "   "])
def test_sin_la_clave_no_arranca_y_el_mensaje_nombra_la_variable(clave):
    entorno = {"SUPABASE_URL": URL} | ({} if clave is None else {"SUPABASE_SERVICE_ROLE_KEY": clave})
    with pytest.raises(ValueError, match="SUPABASE_SERVICE_ROLE_KEY"):
        borrador_desde_entorno(entorno)


def test_con_clave_y_sin_url_no_arranca_y_nombra_la_variable():
    with pytest.raises(ValueError, match="SUPABASE_URL") as e:
        borrador_desde_entorno({"SUPABASE_SERVICE_ROLE_KEY": CLAVE})
    assert CLAVE not in str(e.value)


def test_una_url_insegura_impide_arrancar():
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        borrador_desde_entorno({"SUPABASE_URL": "http://proy.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": CLAVE})


def test_el_cliente_se_crea_una_vez_al_arrancar_con_timeout_explicito(monkeypatch):
    creados = []

    class ClienteFalso:
        def __init__(self, *args, **kwargs):
            creados.append((args, kwargs))

    monkeypatch.setattr(logos, "ClienteAlmacenamiento", ClienteFalso)
    borrador_desde_entorno({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": CLAVE})
    ((args, kwargs),) = creados
    assert args == (URL, CLAVE) and kwargs == {"bucket": BUCKET_LOGOS, "timeout": TIMEOUT_STORAGE_SEG}
    assert 0 < TIMEOUT_STORAGE_SEG <= 30


async def test_cerrar_cierra_el_cliente_http():
    http = httpx.AsyncClient(transport=httpx.MockTransport(_respuesta([])))
    borrador = borrador_desde_entorno({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": CLAVE}, http=http)
    await borrador.cerrar()
    assert http.is_closed


async def test_la_clave_no_aparece_en_el_repr_ni_en_los_errores_ni_en_los_logs(caplog):
    borrador = _borrador(_respuesta([], 500, {"message": f"detalle {CLAVE}"}))
    with caplog.at_level(logging.DEBUG), pytest.raises(AlmacenamientoNoDisponible) as e:
        await borrador(TENANT, RUTA)
    assert CLAVE not in repr(borrador) and CLAVE not in str(e.value) and CLAVE not in caplog.text
    assert RUTA not in caplog.text
