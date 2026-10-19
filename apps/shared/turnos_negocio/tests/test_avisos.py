"""Texto compartido del aviso de vencimiento: contenido, sin PII y un único lugar donde se define."""

import re
from datetime import datetime, timezone
from pathlib import Path

from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

UTC = timezone.utc  # noqa: UP017

RAIZ = Path(__file__).resolve().parents[4]


def test_el_texto_dice_dia_hora_local_y_servicio():
    # 2026-10-21 15:30 UTC = miércoles 12:30 en Buenos Aires (UTC-3)
    texto = mensaje_vencimiento(datetime(2026, 10, 21, 15, 30, tzinfo=UTC), "Corte de pelo")
    assert "Corte de pelo" in texto
    assert "miércoles 21/10 a las 12:30" in texto
    assert "venció" in texto and "cancelada" in texto


def test_la_hora_se_pasa_a_la_zona_del_negocio_cruzando_de_dia():
    texto = mensaje_vencimiento(datetime(2026, 10, 22, 1, 0, tzinfo=UTC), "Color")
    assert "miércoles 21/10 a las 22:00" in texto


def test_el_texto_no_lleva_datos_del_cliente():
    # la función solo recibe turno y servicio: no hay por dónde colar nombre ni teléfono
    import inspect

    assert list(inspect.signature(mensaje_vencimiento).parameters) == ["inicio", "servicio"]


def test_la_plantilla_es_turno_vencido():
    assert PLANTILLA_VENCIMIENTO == "turno_vencido"


def test_el_job_y_el_bot_no_definen_su_propio_texto_ni_plantilla():
    for ruta in (
        "apps/jobs/jobs_vencimiento/proceso.py",
        "apps/shared/turnos_dominio/avisos.py",
    ):
        fuente = (RAIZ / ruta).read_text(encoding="utf-8")
        assert "venció sin respuesta" not in fuente, f"{ruta} define su propio texto"
        assert not re.search(r"""["']turno_vencido["']""", fuente), f"{ruta} define su propia plantilla"
        assert "mensaje_vencimiento" in fuente, f"{ruta} no usa el texto compartido"
