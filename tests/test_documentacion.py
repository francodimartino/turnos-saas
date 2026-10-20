"""Los documentos versionados describen el sistema tal como es, sin citar herramientas externas al proyecto."""

import re
from pathlib import Path

from git_aislado import run_git

RAIZ = Path(__file__).resolve().parent.parent

RAIZ_DOCUMENTOS = ["README.md", "AGENTS.md", "ARCHITECTURE.md"]


def _documentos() -> list[str]:
    """Raíz más todos los .md de docs/ (también subcarpetas): un documento nuevo queda cubierto sin tocar el test."""
    en_docs = sorted(p.relative_to(RAIZ).as_posix() for p in (RAIZ / "docs").rglob("*.md"))
    return RAIZ_DOCUMENTOS + en_docs


DOCUMENTOS = _documentos()

# "auditoría" a secas es una funcionalidad del producto (audit_log); lo prohibido es citar una auditoría externa.
PROHIBIDOS = {
    "auditoría externa": (
        r"auditor[ií]a\s+(externa|de seguridad|previa|inicial)|(informe|hallazgos?)\s+de\s+(la\s+)?auditor[ií]a"
    ),
    "olas de trabajo": r"\bola\s*\d+\b|\bolas?\s+de\s+(remediaci|trabajo)",
    "remediación": r"remediaci[oó]n",
    "arnés": r"\barn[eé]s\b|ARNES_DESARROLLO|\.arnes",
    "roles de agentes": r"\b(builder|evaluator|evaluador|task-decomposer|infra-builder)\b",
    "números de tarea": r"\btareas?\s+(#?\d+|siguiente|anterior)\b|\bplan\s+de\s+(eficiencia|trabajo)\b",
    "decisiones internas": r"decisi[oó]n[^\n]{0,30}\bD\d+\b|\(D\d+\)",
    "id de hallazgo": r"\b(hallazgo|finding)\s*#?[A-Z]*-?\d+\b|\b(H|F|SEC|AUD|HAL)-\d+\b",
}


# Archivos que nombran esos términos a propósito: este test y los que verifican que el producto no los contiene.
EXCLUIDOS_DEL_BARRIDO = {"tests/test_documentacion.py", "tests/test_despliegue.py", "tests/test_migracion_conexion.py"}
EXTENSIONES_BINARIAS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".pdf", ".zip", ".svg"}


def _archivos_versionados() -> list[str]:
    salida = run_git("-C", str(RAIZ), "ls-files", "-z", text=False).stdout.decode("utf-8")
    return sorted(
        ruta
        for ruta in salida.split("\x00")
        if ruta
        and ruta not in EXCLUIDOS_DEL_BARRIDO
        and Path(ruta).suffix.lower() not in EXTENSIONES_BINARIAS
        and not ruta.endswith(("package-lock.json", ".lock"))
    )


def _hallazgos_en(ruta: Path) -> list[str]:
    hallazgos = []
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        for nombre, patron in PROHIBIDOS.items():
            if re.search(patron, linea, re.IGNORECASE):
                hallazgos.append(f"{ruta.name}:{numero} ({nombre})")
    return hallazgos


def test_los_documentos_existen():
    for ruta in DOCUMENTOS:
        assert (RAIZ / ruta).is_file(), ruta


def test_ningun_documento_cita_una_auditoria_ni_herramientas_externas():
    hallazgos = []
    for ruta in DOCUMENTOS:
        hallazgos += [f"{ruta} -> {h}" for h in _hallazgos_en(RAIZ / ruta)]
    assert not hallazgos, hallazgos


def test_ningun_archivo_versionado_cita_planificacion_interna():
    """Código, migraciones, tests, scripts y workflows explican el porqué técnico, no el plan que los originó."""
    hallazgos = []
    for ruta in _archivos_versionados():
        try:
            (RAIZ / ruta).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binario o borrado en el árbol de trabajo
        hallazgos += [f"{ruta} -> {h}" for h in _hallazgos_en(RAIZ / ruta)]
    assert not hallazgos, hallazgos


def test_el_barrido_cubre_codigo_migraciones_y_workflows():
    archivos = _archivos_versionados()
    assert any(a.endswith(".py") for a in archivos)
    assert any(a.startswith("supabase/migrations/") and a.endswith(".sql") for a in archivos)
    assert any(a.startswith(".github/") for a in archivos)


def test_el_barrido_detecta_citas_en_comentarios_de_codigo_y_sql(tmp_path):
    for nombre, texto in (
        ("modulo.py", '"""Anclaje de cabezas (Ola 4, tarea 15)."""\n'),
        ("migracion.sql", "-- Cabezas de la cadena (decisión D6).\n"),
        ("flujo.py", "# Lo pide el flujo de reserva (tarea siguiente).\n"),
        ("ci.sh", "# lo copia infra-builder\n"),
    ):
        archivo = tmp_path / nombre
        archivo.write_text(texto, encoding="utf-8")
        assert _hallazgos_en(archivo), nombre


def test_el_barrido_no_marca_terminos_tecnicos_legitimos(tmp_path):
    archivo = tmp_path / "modulo.py"
    archivo.write_text(
        "# tarea de asyncio en segundo plano; audit_log y audit_chain_heads; hay_hallazgo() del escaneo\n"
        "('00000000-0000-0000-0000-0000000000d1', 'D1', 'idx-1')  -- pack d1, depósito del 301\n",
        encoding="utf-8",
    )
    assert _hallazgos_en(archivo) == []


def test_la_cobertura_incluye_decisiones_y_local():
    assert "docs/decisiones/2026-10-04-proteccion-main.md" in DOCUMENTOS
    assert "docs/local.md" in DOCUMENTOS and "docs/deploy.md" in DOCUMENTOS


def test_el_detector_encuentra_cada_categoria(tmp_path):
    citas = {
        "auditoría externa": "Según la auditoría externa se cambió esto.",
        "olas de trabajo": "Se hizo en la ola 5.",
        "remediación": "Parte de la remediación.",
        "arnés": "Lo corre el arnés.",
        "roles de agentes": "Lo escribió el builder.",
        "números de tarea": "Ver tarea 12.",
        "id de hallazgo": "Corrige el hallazgo H-12.",
        "decisiones internas": "Según la decisión D6 del plan.",
    }
    assert set(citas) == set(PROHIBIDOS)
    for categoria, texto in citas.items():
        archivo = tmp_path / "doc.md"
        archivo.write_text(texto + "\n", encoding="utf-8")
        assert [h for h in _hallazgos_en(archivo) if categoria in h], categoria


def test_el_detector_no_marca_la_auditoria_como_funcionalidad(tmp_path):
    archivo = tmp_path / "doc.md"
    archivo.write_text("El audit_log y la cadena de auditoría del producto.\n", encoding="utf-8")
    assert _hallazgos_en(archivo) == []


def test_readme_no_dice_que_falta_el_codigo():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "sin código de producto" not in readme.lower()


def test_agents_pide_actualizar_docstring_y_arquitectura():
    agents = (RAIZ / "AGENTS.md").read_text(encoding="utf-8")
    assert "docstring del módulo" in agents and "`ARCHITECTURE.md`" in agents


def test_deploy_pide_borrar_calendar_oauth_state_secret_de_bot_y_purga():
    deploy = (RAIZ / "docs/deploy.md").read_text(encoding="utf-8")
    checklist = deploy[deploy.index("## Checklist del día del deploy") : deploy.index("## Primer deploy, paso a paso")]
    patron = r"CALENDAR_OAUTH_STATE_SECRET.{0,80}borrada.{0,200}turnos-bot.{0,40}turnos-job-purga"
    assert re.search(patron, checklist, re.S)


def _arquitectura() -> str:
    return (RAIZ / "ARCHITECTURE.md").read_text(encoding="utf-8")


def test_arquitectura_documenta_lo_que_cambio():
    texto = _arquitectura()
    for esperado in (
        "hmac_clave_unica",
        "/audit-log/verificar-cadena",
        "/plataforma/audit-log/verificar-cadena",
        "/limpiezas-calendar/descartar",
        "audit_chain_anchor",
        "vencimiento-turnos",
        "turnos_negocio",
        "ZONA_HORARIA",
        "tzdata",
        "settings_demasiado_grandes",
    ):
        assert esperado in texto, esperado


def test_arquitectura_acota_la_validacion_de_settings_al_dashboard():
    assert "validación en el dashboard-backend" in _arquitectura()
