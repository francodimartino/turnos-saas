"""Render compartido de los avisos de turno: lo que el reescaneo del outbox manda coincide con el envío inmediato."""

from datetime import datetime, timezone

import pytest

from turnos_negocio import (
    PLANTILLA_RESERVA_CONFIRMADA,
    PLANTILLA_RESERVA_PENDIENTE,
    PLANTILLA_VENCIMIENTO,
    PLANTILLAS_DASHBOARD,
    TEXTO_CONFIRMADO,
    mensaje_aviso_turno,
    mensaje_vencimiento,
    plantillas_conocidas,
)

UTC = timezone.utc  # noqa: UP017
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)  # miércoles 21/10 12:30 en Buenos Aires


def test_vencimiento_usa_el_texto_compartido():
    assert mensaje_aviso_turno(PLANTILLA_VENCIMIENTO, servicio="Corte", profesional=None, inicio=INICIO) == (
        mensaje_vencimiento(INICIO, "Corte")
    )


@pytest.mark.parametrize("plantilla", sorted(PLANTILLAS_DASHBOARD))
def test_plantillas_del_dashboard(plantilla):
    texto = mensaje_aviso_turno(plantilla, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert "Corte" in texto and "21/10/2026 12:30" in texto
    assert "Ana" not in texto


def test_reserva_confirmada_lleva_servicio_profesional_y_fecha_del_bot():
    texto = mensaje_aviso_turno(PLANTILLA_RESERVA_CONFIRMADA, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == f"{TEXTO_CONFIRMADO}\nCorte con Ana, miércoles 21/10 a las 12:30."


def test_reserva_pendiente_dice_hasta_cuando_se_aprueba():
    vence = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)
    texto = mensaje_aviso_turno(
        PLANTILLA_RESERVA_PENDIENTE, servicio="Corte", profesional="Ana", inicio=INICIO, vence=vence
    )
    assert "antes del martes 20/10 a las 12:30" in texto and texto.endswith(
        "Corte con Ana, miércoles 21/10 a las 12:30."
    )


def test_plantilla_desconocida_devuelve_none_y_las_conocidas_son_todas_renderizables():
    assert mensaje_aviso_turno("otra", servicio="x", profesional=None, inicio=INICIO) is None
    for plantilla in plantillas_conocidas():
        assert mensaje_aviso_turno(plantilla, servicio="x", profesional="y", inicio=INICIO) is not None
