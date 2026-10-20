"""Los paquetes compartidos y el del bot llevan el prefijo `turnos_`; los nombres anteriores no vuelven a aparecer."""

import re
from pathlib import Path

from git_aislado import run_git

RAIZ = Path(__file__).resolve().parent.parent
RETIRADOS = ("observabilidad", "proteccion_login", "seguridad_http", "supabase_proxy", "telefonos", "bot_app")
# Referencias a un módulo Python con el nombre viejo: import, ruta de carpeta o nombre de logger.
_N = "|".join(RETIRADOS)
PATRON = re.compile(
    rf"(^\s*(?:from|import)\s+(?:{_N}))|(apps/shared/(?:{_N}))|([\"'](?:{_N})\.\w)",
    re.MULTILINE,
)
EXTENSIONES = (
    *(".py", ".md", ".yml", ".yaml", ".toml", ".ts", ".tsx"),
    *(".js", ".mjs", ".sh", ".sql", ".json", ".cfg", ".ini"),
)
GENERADOS = ("node_modules/", "/dist/", "src/api/generado", "package-lock.json")
ESTE = Path(__file__).name


def _versionados():
    salida = run_git("ls-files", "-z", cwd=RAIZ).stdout
    return [RAIZ / f for f in salida.split("\0") if f.endswith(EXTENSIONES) or Path(f).name.startswith("Dockerfile")]


def test_las_carpetas_compartidas_tienen_prefijo_turnos():
    nombres = {d.name for d in (RAIZ / "apps" / "shared").iterdir() if (d / "__init__.py").is_file()}
    assert nombres and all(n.startswith("turnos_") for n in nombres), nombres
    assert not nombres & set(RETIRADOS)


def test_el_paquete_del_bot_tiene_prefijo_turnos():
    nombres = {d.name for d in (RAIZ / "apps" / "bot").iterdir() if (d / "__init__.py").is_file()}
    assert nombres == {"turnos_bot"}, nombres


def test_ninguna_referencia_a_los_nombres_retirados():
    hallazgos = []
    for archivo in _versionados():
        rel = archivo.relative_to(RAIZ).as_posix()
        if archivo.name == ESTE or not archivo.is_file() or any(g in rel for g in GENERADOS):
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        if PATRON.search(texto):
            hallazgos.append(str(archivo.relative_to(RAIZ)))
    assert not hallazgos, hallazgos


# Nombre sin prefijo cuyo uso es inequívoco en cualquier contexto (comandos de arranque, rutas, docs).
NOMBRE_BOT_VIEJO = re.compile(r"\bbot_app\b")


def _menciones(texto):
    return NOMBRE_BOT_VIEJO.findall(texto)


def test_bot_app_no_aparece_en_ningun_archivo_versionado():
    hallazgos = []
    for archivo in _versionados():
        rel = archivo.relative_to(RAIZ).as_posix()
        if archivo.name == ESTE or not archivo.is_file() or any(g in rel for g in GENERADOS):
            continue
        if _menciones(archivo.read_text(encoding="utf-8", errors="ignore")):
            hallazgos.append(rel)
    assert not hallazgos, hallazgos


def test_el_chequeo_de_bot_app_detecta_comandos_rutas_y_docs(tmp_path):
    ejemplo = tmp_path / "ejemplo.yaml"
    ejemplo.write_text(
        "startCommand: uvicorn bot_app.main:app\nruta: apps/bot/bot_app/app.py\nver `bot_app`\n", encoding="utf-8"
    )
    assert len(_menciones(ejemplo.read_text(encoding="utf-8"))) == 3
    assert not _menciones("uvicorn turnos_bot.main:app y apps/bot/turnos_bot")


def test_los_dolares_dobles_estan_balanceados_en_sql_y_docs():
    """Detecta la corrupción de `$$` (un bloque `as $$ ... $$` que pierde una de las marcas)."""
    desbalanceados = []
    for archivo in _versionados():
        if archivo.suffix not in (".sql", ".md") or not archivo.is_file():
            continue
        if archivo.name == ESTE:
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        if texto.count("$$") % 2:
            desbalanceados.append(archivo.relative_to(RAIZ).as_posix())
    assert not desbalanceados, desbalanceados


def test_cada_funcion_sql_de_architecture_abre_y_cierra_su_cuerpo():
    texto = (RAIZ / "ARCHITECTURE.md").read_text(encoding="utf-8")
    patron = r"create (?:or replace )?function.*?(?=\ncreate |```)"
    for bloque in re.findall(patron, texto, re.DOTALL | re.IGNORECASE):
        if " as $$" in bloque:
            assert bloque.count("$$") == 2, bloque[:120]
