"""Lo compartido entre flujos del bot: una sola lista de palabras de cancelar, normalizar y la acción de Calendar."""

import logging
import re
import uuid
from pathlib import Path

import httpx
import pytest
from platform_calendar import ErrorApiCalendar

from turnos_bot import compartido
from turnos_bot.compartido import PALABRAS_CANCELAR, a_uuid, normalizar, pide_cancelar_o_reprogramar
from turnos_bot.despachador import Recursos

RAIZ = Path(__file__).resolve().parents[1]


def test_la_lista_de_palabras_es_la_union_de_las_que_tenian_los_flujos():
    assert {
        "cancelar",
        "cancelo",
        "cancelacion",
        "reprogramar",
        "reprogramo",
        "posponer",
        "anular",
        "anulo",
    } == PALABRAS_CANCELAR


@pytest.mark.parametrize("palabra", sorted(PALABRAS_CANCELAR))
def test_cada_palabra_se_reconoce_sola_con_barra_con_acentos_y_en_una_frase(palabra):
    assert pide_cancelar_o_reprogramar(palabra)
    assert pide_cancelar_o_reprogramar(f"/{palabra}")
    assert pide_cancelar_o_reprogramar(f"  Quiero {palabra.upper()} mi turno ")


def test_la_cancelacion_con_tilde_tambien_se_reconoce():
    assert pide_cancelar_o_reprogramar("Cancelación")


@pytest.mark.parametrize("texto", ["hola", "mis turnos", "cancelado", "", None, "servicios"])
def test_otros_textos_no_piden_cancelar(texto):
    assert pide_cancelar_o_reprogramar(texto) is False


def test_normalizar_saca_acentos_pasa_a_minusculas_y_colapsa_espacios():
    assert normalizar("  ¿Qué  TAL, Ñandú? ") == "¿que tal, nandu?"
    assert normalizar(None) == "" and normalizar("") == ""


def test_a_uuid():
    valor = uuid.uuid4()
    assert a_uuid(str(valor)) == valor and a_uuid("no-es-uuid") is None and a_uuid("") is None


def test_ningun_flujo_tiene_su_propia_copia_de_lo_compartido():
    copias = []
    for archivo in RAIZ.rglob("*.py"):
        if "tests" in archivo.parts or archivo.name == "compartido.py":
            continue
        texto = archivo.read_text(encoding="utf-8")
        if re.search(
            r"^_?PALABRAS_CANCELAR\s*=|^def _?normalizar\(|^def _uuid\(|^(HORIZONTE_DIAS|HORARIOS_POR_PAGINA) =",
            texto,
            re.M,
        ):
            copias.append(archivo.name)
    assert copias == []


# --- acción de Calendar -------------------------------------------------------------------------------------


def recursos(cliente) -> Recursos:
    return Recursos(pool=object(), cliente_http=cliente)


async def test_la_accion_usa_el_cliente_http_del_webhook_y_no_lo_cierra():
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    vistos = []

    async def sincronizar(rec, http):
        vistos.append(http)

    await compartido.accion_de_calendario(sincronizar, logging.getLogger("prueba"))(recursos(cliente))
    assert vistos == [cliente] and not cliente.is_closed
    await cliente.aclose()


async def test_sin_cliente_http_abre_uno_propio_y_lo_cierra_aunque_falle():
    vistos = []

    async def sincronizar(rec, http):
        vistos.append(http)
        raise ErrorApiCalendar(503)

    await compartido.accion_de_calendario(sincronizar, logging.getLogger("prueba"))(recursos(None))
    assert len(vistos) == 1 and vistos[0].is_closed


async def test_un_error_de_calendar_se_loguea_sin_propagarse_ni_datos(caplog):
    async def sincronizar(rec, http):
        raise ErrorApiCalendar(503)

    with caplog.at_level(logging.WARNING, logger="prueba"):
        await compartido.accion_de_calendario(sincronizar, logging.getLogger("prueba"))(recursos(None))
    assert "calendar_sync_fallido" in caplog.text
