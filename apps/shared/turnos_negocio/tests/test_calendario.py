from datetime import datetime, timedelta

from turnos_negocio.calendario import TEXTO_EVENTO_ANONIMIZADO, evento_de_turno

INICIO = datetime.fromisoformat("2026-11-03T15:00:00+00:00")
FIN = INICIO + timedelta(minutes=45)


def _evento(**kw):
    datos = dict(
        servicio="Corte de pelo",
        cliente_nombre="Ana Pérez",
        cliente_anonimizado=False,
        inicio=INICIO,
        fin=FIN,
    )
    datos.update(kw)
    return evento_de_turno(**datos)


def test_el_evento_lleva_servicio_y_cliente_con_el_texto_de_siempre():
    e = _evento(id_externo="evt-1")
    assert e.titulo == "Corte de pelo — Ana Pérez"
    assert e.descripcion == "Servicio: Corte de pelo\nCliente: Ana Pérez"
    assert (e.inicio, e.fin, e.id_externo) == (INICIO, FIN, "evt-1")


def test_un_cliente_anonimizado_no_aporta_datos_ni_se_inventan():
    e = _evento(cliente_nombre="Ana Pérez", cliente_anonimizado=True)
    assert "Ana" not in e.titulo + e.descripcion
    assert e.titulo == "Corte de pelo — (cliente anonimizado)"


def test_sin_nombre_el_cliente_se_muestra_generico():
    assert _evento(cliente_nombre=None).titulo == "Corte de pelo — Cliente"


def test_el_texto_neutro_es_el_de_siempre():
    assert TEXTO_EVENTO_ANONIMIZADO == ("Turno (cliente anonimizado)", "Datos del cliente eliminados.")
