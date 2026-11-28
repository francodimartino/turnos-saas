"""Render compartido de los avisos de turno: lo que el reescaneo del outbox manda coincide con el envío inmediato."""

from datetime import datetime, timezone

import pytest

from turnos_negocio import (
    PLANTILLA_AVISO_NEGOCIO_APROBACION,
    PLANTILLA_AVISO_NEGOCIO_CANCELACION,
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO,
    PLANTILLA_AVISO_NEGOCIO_RESERVA,
    PLANTILLA_CAMBIO_HORARIO_APROBADO,
    PLANTILLA_CAMBIO_HORARIO_RECHAZADO,
    PLANTILLA_CAMBIO_HORARIO_VENCIDO,
    PLANTILLA_RESERVA_CONFIRMADA,
    PLANTILLA_RESERVA_PENDIENTE,
    PLANTILLA_VENCIMIENTO,
    PLANTILLAS_DASHBOARD,
    TEXTO_CONFIRMADO,
    mensaje_aviso_turno,
    mensaje_vencimiento,
    plantillas_cliente,
    plantillas_conocidas,
    plantillas_negocio,
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


def test_turno_cancelado_no_dice_quien_lo_cancelo_porque_lo_encolan_el_negocio_y_el_cliente():
    texto = mensaje_aviso_turno("turno_cancelado", servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Tu turno de Corte del 21/10/2026 12:30 fue cancelado."


def test_turno_movido_dice_el_horario_nuevo():
    texto = mensaje_aviso_turno("turno_movido", servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Tu turno de Corte fue movido para el 21/10/2026 12:30."


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


# --- Avisos nuevos: cambio de horario (al cliente) y avisos al negocio -----------------------------------------------


def test_cambio_horario_aprobado_dice_el_horario_nuevo():
    texto = mensaje_aviso_turno(PLANTILLA_CAMBIO_HORARIO_APROBADO, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Tu cambio de horario de Corte fue aprobado: tu turno quedó para el 21/10/2026 12:30."


def test_cambio_horario_rechazado_dice_que_el_turno_sigue_como_estaba():
    texto = mensaje_aviso_turno(PLANTILLA_CAMBIO_HORARIO_RECHAZADO, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "El negocio no aprobó el cambio de horario de tu turno de Corte: sigue para el 21/10/2026 12:30."


def test_cambio_horario_vencido_dice_que_el_turno_sigue_como_estaba():
    texto = mensaje_aviso_turno(PLANTILLA_CAMBIO_HORARIO_VENCIDO, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == (
        "Tu pedido de cambio de horario de Corte venció sin respuesta del negocio: tu turno sigue para el "
        "21/10/2026 12:30."
    )


def test_aviso_al_negocio_por_reserva():
    texto = mensaje_aviso_turno(PLANTILLA_AVISO_NEGOCIO_RESERVA, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Nueva reserva: Corte con Ana, miércoles 21/10 a las 12:30."


def test_aviso_al_negocio_por_cancelacion():
    texto = mensaje_aviso_turno(PLANTILLA_AVISO_NEGOCIO_CANCELACION, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Se canceló un turno de Corte con Ana del miércoles 21/10 a las 12:30."


def test_aviso_al_negocio_por_movimiento():
    texto = mensaje_aviso_turno(PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Se movió un turno de Corte con Ana: ahora es el miércoles 21/10 a las 12:30."


def test_aviso_al_negocio_por_pedido_de_aprobacion():
    texto = mensaje_aviso_turno(PLANTILLA_AVISO_NEGOCIO_APROBACION, servicio="Corte", profesional="Ana", inicio=INICIO)
    assert texto == "Hay un turno de Corte con Ana, miércoles 21/10 a las 12:30, pendiente de tu aprobación."


def test_aviso_al_negocio_sin_profesional_usa_el_generico():
    texto = mensaje_aviso_turno(PLANTILLA_AVISO_NEGOCIO_RESERVA, servicio="Corte", profesional=None, inicio=INICIO)
    assert texto == "Nueva reserva: Corte con el profesional, miércoles 21/10 a las 12:30."


def test_todas_las_plantillas_de_cliente_y_de_negocio_se_renderizan():
    for plantilla in plantillas_cliente() | plantillas_negocio():
        texto = mensaje_aviso_turno(plantilla, servicio="Corte", profesional="Ana", inicio=INICIO)
        assert texto is not None and "Corte" in texto, plantilla


def test_los_conjuntos_de_plantillas_son_disjuntos_y_el_de_cliente_incluye_los_conocidos():
    assert plantillas_conocidas() <= plantillas_cliente()
    assert not plantillas_cliente() & plantillas_negocio()
    assert plantillas_cliente() - plantillas_conocidas() == {
        PLANTILLA_CAMBIO_HORARIO_APROBADO,
        PLANTILLA_CAMBIO_HORARIO_RECHAZADO,
        PLANTILLA_CAMBIO_HORARIO_VENCIDO,
    }
    assert len(plantillas_negocio()) == 4


def test_plantillas_conocidas_sigue_siendo_solo_las_que_el_reescaneo_sabe_enviar():
    assert PLANTILLA_CAMBIO_HORARIO_APROBADO not in plantillas_conocidas()
    assert PLANTILLA_AVISO_NEGOCIO_RESERVA not in plantillas_conocidas()
