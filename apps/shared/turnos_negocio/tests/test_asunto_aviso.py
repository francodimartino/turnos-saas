"""Asunto del email de cada plantilla de aviso: uno por plantilla y sin datos del cliente."""

import inspect

import pytest

from turnos_negocio import asunto_aviso_turno, plantillas_cliente, plantillas_negocio


def test_cada_plantilla_de_cliente_y_de_negocio_tiene_asunto():
    for plantilla in plantillas_cliente() | plantillas_negocio():
        asunto = asunto_aviso_turno(plantilla)
        assert asunto and asunto.strip() == asunto and len(asunto) <= 100, plantilla


def test_una_plantilla_desconocida_no_tiene_asunto():
    assert asunto_aviso_turno("sincronizacion_calendar") is None
    assert asunto_aviso_turno("otra") is None


def test_el_asunto_solo_depende_de_la_plantilla():
    assert list(inspect.signature(asunto_aviso_turno).parameters) == ["plantilla"]


@pytest.mark.parametrize("plantilla", sorted(plantillas_cliente()))
def test_los_asuntos_de_cliente_no_dicen_negocio(plantilla):
    assert "nueva reserva" not in (asunto_aviso_turno(plantilla) or "").lower()
