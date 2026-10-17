"""Chequeos estáticos (sin base de datos) de la migración que deja notifications_log solo-por-función.

- Revoca INSERT y UPDATE de bot_service y dashboard_service sobre notifications_log y no revoca SELECT.
- No toca a otros roles ni a otras tablas.
- La plantilla de plataforma es idéntica a la migración (no hay nada de turnos).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001300_notifications_log_solo_por_funcion.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _sentencias(ruta: Path) -> list[str]:
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("--")]
    return [re.sub(r"\s+", " ", s).strip() for s in " ".join(lineas).split(";") if s.strip()]


def test_revoca_insert_y_update_directos_y_conserva_select():
    assert _sentencias(MIGRACION) == [
        "revoke insert, update on public.notifications_log from bot_service, dashboard_service"
    ]


def test_la_plantilla_es_identica_a_la_migracion():
    assert _sentencias(MIGRACION) == _sentencias(PLANTILLA)
