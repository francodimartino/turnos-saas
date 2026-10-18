"""El dashboard formatea los turnos con una zona IANA real (no un desfase fijo) y coincide con el bot."""

from datetime import datetime, timezone  # noqa: UP017
from zoneinfo import ZoneInfo

from dashboard_backend import turnos

NUEVA_YORK = ZoneInfo("America/New_York")  # tiene horario de verano


def test_la_zona_del_dashboard_es_buenos_aires():
    assert turnos.ZONA_HORARIA.key == "America/Argentina/Buenos_Aires"


def test_cuando_sigue_a_la_zona_inyectada_en_invierno_y_en_verano():
    invierno = datetime(2030, 1, 15, 15, 0, tzinfo=timezone.utc)  # noqa: UP017
    verano = datetime(2030, 7, 15, 15, 0, tzinfo=timezone.utc)  # noqa: UP017
    assert turnos.cuando(invierno, NUEVA_YORK) == "15/01/2030 10:00"  # UTC-5
    assert turnos.cuando(verano, NUEVA_YORK) == "15/07/2030 11:00"  # UTC-4


def test_cuando_por_defecto_muestra_hora_argentina():
    assert turnos.cuando(datetime(2030, 7, 15, 15, 0, tzinfo=timezone.utc)) == "15/07/2030 12:00"  # noqa: UP017
