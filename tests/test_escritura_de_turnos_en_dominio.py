"""El alta y el movimiento de turnos tienen una sola implementación: `turnos_dominio.alta`.

Fuera de `supabase/` (esquema, migraciones, funciones de la base) y de los tests, ningún archivo de código puede
tener su propio `insert into public.bookings` ni un `update public.bookings set time_range`: bot y dashboard
crean y mueven turnos con `crear_turno` y `mover_turno`.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOMINIO = Path("apps/shared/turnos_dominio/alta.py")
CARPETAS = ("apps", "platform", "scripts", "tools")
OMITIR = {"node_modules", ".git", "__pycache__", ".venv", "venv"}

ALTA = re.compile(r"insert\s+into\s+(public\.)?bookings\b", re.IGNORECASE)
MOVER = re.compile(r"update\s+(public\.)?bookings\s+set\s+time_range\b", re.IGNORECASE)


def _es_de_test(ruta: Path) -> bool:
    return "tests" in ruta.parts or ruta.name.startswith("test_") or ruta.name == "conftest.py"


def _texto(ruta: Path) -> str:
    # Une literales de cadena adyacentes en líneas distintas: "update public.bookings " "set time_range ..."
    return re.sub(r'"\s*\n\s*"', "", ruta.read_text(encoding="utf-8"))


def infracciones(raiz: Path) -> list[str]:
    encontradas = []
    for carpeta in CARPETAS:
        for ruta in sorted((raiz / carpeta).rglob("*.py")):
            relativa = ruta.relative_to(raiz)
            if OMITIR & set(relativa.parts) or _es_de_test(relativa) or relativa == DOMINIO:
                continue
            texto = _texto(ruta)
            if ALTA.search(texto):
                encontradas.append(f"{relativa.as_posix()}: insert into bookings")
            if MOVER.search(texto):
                encontradas.append(f"{relativa.as_posix()}: update bookings set time_range")
    return encontradas


def test_solo_el_dominio_inserta_y_mueve_turnos():
    assert infracciones(RAIZ) == [], "el alta y el movimiento de turnos viven en turnos_dominio.alta"


def test_el_dominio_es_quien_tiene_las_dos_sentencias():
    texto = _texto(RAIZ / DOMINIO)
    assert ALTA.search(texto) and MOVER.search(texto)


def test_el_detector_ve_un_insert_y_un_update_de_rango_ajenos_al_dominio(tmp_path):
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "canal.py").write_text(
        'A = "insert into public.bookings (id) values ($1)"\n'
        'B = (\n    "update public.bookings "\n    "set time_range = $1"\n)\n',
        encoding="utf-8",
    )
    (tmp_path / "apps" / "otro.py").write_text('C = "update public.bookings set status = $1"\n', encoding="utf-8")
    assert infracciones(tmp_path) == [
        "apps/canal.py: insert into bookings",
        "apps/canal.py: update bookings set time_range",
    ]
