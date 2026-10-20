"""La zona horaria del negocio se define en un único módulo; el resto la importa."""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
UNICO = RAIZ / "apps" / "shared" / "turnos_negocio" / "zona.py"
PATRON = re.compile(r"ZoneInfo\(|timezone\(\s*timedelta")
IGNORADOS = {"tests", "node_modules", ".venv", "venv", "__pycache__"}


def _fuentes():
    for base in ("apps", "platform"):
        for ruta in (RAIZ / base).rglob("*.py"):
            if not IGNORADOS.intersection(ruta.relative_to(RAIZ).parts):
                yield ruta


def test_no_hay_otra_definicion_de_zona_horaria():
    fuentes = list(_fuentes())
    assert UNICO in fuentes
    sobrantes = [
        str(r.relative_to(RAIZ)) for r in fuentes if r != UNICO and PATRON.search(r.read_text(encoding="utf-8"))
    ]
    assert not sobrantes, f"zona horaria definida fuera del módulo único: {sobrantes}"


def test_bot_dashboard_y_aviso_usan_la_misma_zona():
    from dashboard_backend import turnos
    from turnos_bot.consultas import flujo
    from turnos_dominio import consulta, huecos

    from turnos_negocio import ZONA_HORARIA, avisos

    assert huecos.ZONA_HORARIA is ZONA_HORARIA
    assert consulta.ZONA_HORARIA is ZONA_HORARIA
    assert flujo.ZONA_HORARIA is ZONA_HORARIA
    assert turnos.ZONA_HORARIA is ZONA_HORARIA
    assert avisos.ZONA_HORARIA is ZONA_HORARIA


def test_platform_notifications_no_define_zona_ni_textos_de_turnos():
    import platform_notifications

    assert not hasattr(platform_notifications, "ZONA_NEGOCIO")
    assert not hasattr(platform_notifications, "mensaje_vencimiento")
