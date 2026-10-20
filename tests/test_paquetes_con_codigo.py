"""Todo paquete de `platform/packages` tiene código: no hay paquetes de relleno.

Un paquete que es solo un `__init__.py` vacío o de una línea (una docstring) no aporta nada y engaña a las listas
de paquetes; se crea cuando hay algo que poner adentro.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PAQUETES = RAIZ / "platform" / "packages"


def _lineas_con_contenido(archivo: Path) -> int:
    return sum(1 for linea in archivo.read_text(encoding="utf-8").splitlines() if linea.strip())


def paquetes_sin_codigo(raiz: Path) -> list[str]:
    """Nombres de los paquetes de `raiz` cuyo único módulo es un `__init__.py` de hasta una línea."""
    vacios = []
    for carpeta in sorted(p for p in raiz.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        modulos = [m for m in carpeta.rglob("*.py") if "__pycache__" not in m.parts]
        otros = [m for m in modulos if m.name != "__init__.py"]
        inits = [m for m in modulos if m.name == "__init__.py"]
        if not otros and all(_lineas_con_contenido(i) <= 1 for i in inits):
            vacios.append(carpeta.name)
    return vacios


def test_ningun_paquete_de_platform_esta_sin_codigo():
    assert PAQUETES.is_dir()
    assert paquetes_sin_codigo(PAQUETES) == []


def test_detecta_un_paquete_con_init_de_una_linea(tmp_path):
    (tmp_path / "platform_x" / "platform_x").mkdir(parents=True)
    (tmp_path / "platform_x" / "platform_x" / "__init__.py").write_text('"""platform_x."""\n', encoding="utf-8")
    assert paquetes_sin_codigo(tmp_path) == ["platform_x"]


def test_detecta_un_paquete_con_init_vacio(tmp_path):
    (tmp_path / "platform_y" / "platform_y").mkdir(parents=True)
    (tmp_path / "platform_y" / "platform_y" / "__init__.py").write_text("", encoding="utf-8")
    assert paquetes_sin_codigo(tmp_path) == ["platform_y"]


def test_no_marca_un_paquete_con_otro_modulo(tmp_path):
    (tmp_path / "platform_z" / "platform_z").mkdir(parents=True)
    (tmp_path / "platform_z" / "platform_z" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "platform_z" / "platform_z" / "logica.py").write_text("X = 1\n", encoding="utf-8")
    assert paquetes_sin_codigo(tmp_path) == []


def test_agents_lista_los_paquetes_platform_que_existen():
    """La línea `platform/` de AGENTS.md nombra los paquetes de `platform/packages`, ni más ni menos."""
    linea = next(
        linea
        for linea in (RAIZ / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        if linea.startswith("platform/") and "Paquetes Python" in linea
    )
    entre_parentesis = linea.split("(", 1)[1].split(")", 1)[0]
    # Lo que va después de ";" es una aclaración, no un paquete.
    listados = {p.strip() for p in entre_parentesis.split(";")[0].split(",")}
    existentes = {
        c.name.removeprefix("platform_") for c in PAQUETES.iterdir() if c.is_dir() and c.name.startswith("platform_")
    }
    assert listados == existentes


def paquetes_python(raiz: Path) -> set[str]:
    """Nombres de los paquetes Python de `apps/` y `platform/packages`.

    Un paquete es una carpeta con `__init__.py`: hija directa de cada app (`turnos_bot`, `dashboard_backend`), de
    `apps/shared` y `apps/jobs`, o de la carpeta de cada paquete de `platform/packages`.
    """
    nombres = set()
    for app in (raiz / "apps").iterdir():
        if app.is_dir() and not app.name.startswith("."):
            nombres |= {c.name for c in app.iterdir() if (c / "__init__.py").is_file()}
    for paquete in (raiz / "platform" / "packages").iterdir():
        if paquete.is_dir():
            nombres |= {c.name for c in paquete.iterdir() if (c / "__init__.py").is_file()}
    return nombres


def paquetes_sin_mencion(paquetes: set[str], documento: str) -> list[str]:
    """Los paquetes que `documento` no nombra (como palabra completa)."""
    return sorted(p for p in paquetes if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)}(?![A-Za-z0-9_])", documento))


def test_arquitectura_menciona_cada_paquete_de_apps_y_platform():
    paquetes = paquetes_python(RAIZ)
    assert {"turnos_bot", "jobs_purga", "platform_tenancy", "turnos_negocio"} <= paquetes
    documento = (RAIZ / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert paquetes_sin_mencion(paquetes, documento) == []


def test_detecta_un_paquete_no_mencionado_en_el_documento():
    assert paquetes_sin_mencion({"turnos_bot", "turnos_nuevo"}, "usa turnos_bot y turnos_nuevo_x") == ["turnos_nuevo"]


def test_detecta_un_paquete_nuevo_en_el_arbol(tmp_path):
    (tmp_path / "apps" / "shared" / "turnos_nuevo").mkdir(parents=True)
    (tmp_path / "apps" / "shared" / "turnos_nuevo" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "platform" / "packages" / "platform_x" / "platform_x").mkdir(parents=True)
    (tmp_path / "platform" / "packages" / "platform_x" / "platform_x" / "__init__.py").write_text("", encoding="utf-8")
    assert paquetes_python(tmp_path) == {"turnos_nuevo", "platform_x"}
