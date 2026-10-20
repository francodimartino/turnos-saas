"""Todo paquete de `platform/packages` tiene código: no hay paquetes de relleno.

Un paquete que es solo un `__init__.py` vacío o de una línea (una docstring) no aporta nada y engaña a las listas
de paquetes; se crea cuando hay algo que poner adentro.
"""

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
