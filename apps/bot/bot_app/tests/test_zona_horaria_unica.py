"""La zona horaria se define en un único lugar por aplicación; el resto la importa."""

import re
from pathlib import Path

APPS = Path(__file__).resolve().parents[3]
PERMITIDOS = {
    APPS / "bot" / "disponibilidad" / "huecos.py",  # el bot
    APPS / "dashboard-backend" / "dashboard_backend" / "turnos.py",  # el dashboard (otra app, sin dependencia del bot)
}
PATRON = re.compile(r"ZoneInfo\(|timezone\(\s*timedelta")


def _fuentes():
    for raiz in (APPS / "bot", APPS / "dashboard-backend"):
        for ruta in raiz.rglob("*.py"):
            if "tests" in ruta.parts or ".venv" in ruta.parts:
                continue
            yield ruta


def test_no_hay_otra_definicion_de_zona_horaria():
    fuentes = list(_fuentes())
    assert PERMITIDOS <= set(fuentes)
    sobrantes = [
        str(r.relative_to(APPS))
        for r in fuentes
        if r not in PERMITIDOS and PATRON.search(r.read_text(encoding="utf-8"))
    ]
    assert not sobrantes, f"zona horaria definida fuera del módulo único: {sobrantes}"


def test_la_consulta_importa_la_zona_de_disponibilidad():
    from disponibilidad import ZONA_HORARIA

    from bot_app.consultas import flujo

    assert flujo.ZONA_HORARIA is ZONA_HORARIA


def test_el_dashboard_usa_la_misma_zona_que_el_bot():
    """Las dos apps no comparten código: se compara la zona declarada, así una hora no puede diverger."""
    from disponibilidad import ZONA_HORARIA

    fuente = (APPS / "dashboard-backend" / "dashboard_backend" / "turnos.py").read_text(encoding="utf-8")
    assert f'ZoneInfo("{ZONA_HORARIA.key}")' in fuente
