"""El código de aplicación (bot, dashboard y paquetes) no escribe `notifications_log` con SQL directo.

Toda escritura pasa por `public.record_notification_attempt` (`RegistradorFuncion`): así el rol de aplicación no
necesita INSERT/UPDATE sobre la tabla y la función impone que 'sent' y 'failed' sean estados finales.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ESCRITURA = re.compile(
    r"(insert\s+into|update|delete\s+from|truncate(\s+table)?)\s+(public\.)?notifications_log\b", re.IGNORECASE
)


def _fuentes_de_aplicacion():
    for base in (RAIZ / "apps", RAIZ / "platform" / "packages"):
        for ruta in base.rglob("*.py"):
            partes = set(ruta.relative_to(RAIZ).parts)
            if partes & {"tests", ".venv", "node_modules", "__pycache__"}:
                continue
            yield ruta


def test_hay_codigo_que_revisar():
    assert any(r.name == "webhook.py" for r in _fuentes_de_aplicacion())


def test_ningun_insert_update_ni_delete_directo_sobre_notifications_log():
    infractores = [
        f"{r.relative_to(RAIZ)}: {m.group(0)}"
        for r in _fuentes_de_aplicacion()
        for m in ESCRITURA.finditer(r.read_text(encoding="utf-8"))
    ]
    assert not infractores, infractores


def test_el_detector_reconoce_escrituras_directas():
    for sql in (
        "insert into public.notifications_log (a) values (1)",
        "UPDATE notifications_log set status = 'sent'",
        "delete  from public.notifications_log",
    ):
        assert ESCRITURA.search(sql), sql
