"""El bucket de logos es público de solo lectura: ningún archivo versionado puede decir que es privado."""

import re
from pathlib import Path

from git_aislado import run_git

RAIZ = Path(__file__).resolve().parent.parent
EXTENSIONES = {".md", ".py", ".ts", ".tsx", ".yaml", ".yml", ".example", ".sql", ".toml"}
# "bucket privado", "bucket de logos privado", "bucket `logos` es privado", "private bucket", "logos privados".
AFIRMACION_FALSA = re.compile(
    r"bucket[^\n.]{0,40}\b(privad[oa]s?|private)\b|\b(private|privad[oa])\s+bucket|logos?\s+(es\s+|son\s+)?privad",
    re.IGNORECASE,
)
# El único bucket privado que existe es el de los backups (otro servicio, otro propósito).
LINEA_DE_BACKUPS = re.compile(r"R2|backup", re.IGNORECASE)


def _versionados() -> list[Path]:
    salida = run_git("-C", str(RAIZ), "ls-files", "-z", text=False).stdout.decode("utf-8")
    rutas = [RAIZ / r for r in salida.split("\0") if r]
    return [r for r in rutas if r.suffix in EXTENSIONES and r.is_file() and "node_modules" not in r.parts]


def _infractores(texto: str) -> list[str]:
    return [
        linea.strip()
        for linea in texto.splitlines()
        if AFIRMACION_FALSA.search(linea) and not LINEA_DE_BACKUPS.search(linea)
    ]


def test_ningun_archivo_versionado_dice_que_el_bucket_de_logos_es_privado():
    malos = {}
    for ruta in _versionados():
        if ruta.name == Path(__file__).name:
            continue
        encontrados = _infractores(ruta.read_text(encoding="utf-8", errors="ignore"))
        if encontrados:
            malos[ruta.relative_to(RAIZ).as_posix()] = encontrados
    assert malos == {}


def test_el_detector_marca_las_formas_falsas_y_deja_pasar_las_verdaderas():
    for falsa in (
        "el bucket privado `logos`",
        "El bucket de logos es privado",
        "the logos bucket is private  # private bucket",
        "Public bucket apagado, bucket **privado** de logos",
    ):
        assert _infractores(falsa), falsa
    for cierta in (
        "bucket público de solo lectura `logos`",
        "Crear un bucket **privado** de R2 para los backups",
        "el servicio es privado",
    ):
        assert _infractores(cierta) == [], cierta


def test_los_documentos_dicen_que_es_publico_de_solo_lectura():
    for rel in ("ARCHITECTURE.md", "docs/deploy.md"):
        assert "público de solo lectura" in (RAIZ / rel).read_text(encoding="utf-8"), rel
