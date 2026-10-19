"""Los documentos versionados describen el sistema tal como es, sin citar herramientas externas al proyecto."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

DOCUMENTOS = ["README.md", "AGENTS.md", "ARCHITECTURE.md", "docs/deploy.md"]

# "auditoría" a secas es una funcionalidad del producto (audit_log); lo prohibido es citar una auditoría externa.
PROHIBIDOS = {
    "auditoría externa": (
        r"auditor[ií]a\s+(externa|de seguridad|previa|inicial)|(informe|hallazgos?)\s+de\s+(la\s+)?auditor[ií]a"
    ),
    "olas de trabajo": r"\bola\s*\d+\b|\bolas?\s+de\s+(remediaci|trabajo)",
    "remediación": r"remediaci[oó]n",
    "arnés": r"\barn[eé]s\b|ARNES_DESARROLLO|\.arnes",
    "roles de agentes": r"\b(builder|evaluator|evaluador|task-decomposer|infra-builder)\b",
    "números de tarea": r"\btareas?\s+#?\d+\b",
}


def test_los_documentos_existen():
    for ruta in DOCUMENTOS:
        assert (RAIZ / ruta).is_file(), ruta


def test_ningun_documento_cita_una_auditoria_ni_herramientas_externas():
    hallazgos = []
    for ruta in DOCUMENTOS:
        for numero, linea in enumerate((RAIZ / ruta).read_text(encoding="utf-8").splitlines(), 1):
            for nombre, patron in PROHIBIDOS.items():
                if re.search(patron, linea, re.IGNORECASE):
                    hallazgos.append(f"{ruta}:{numero} ({nombre})")
    assert not hallazgos, hallazgos


def test_readme_no_dice_que_falta_el_codigo():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "sin código de producto" not in readme.lower()


def test_agents_pide_actualizar_docstring_y_arquitectura():
    agents = (RAIZ / "AGENTS.md").read_text(encoding="utf-8")
    assert "docstring del módulo" in agents and "`ARCHITECTURE.md`" in agents


def test_deploy_pide_borrar_calendar_oauth_state_secret_de_bot_y_purga():
    deploy = (RAIZ / "docs/deploy.md").read_text(encoding="utf-8")
    checklist = deploy[deploy.index("## Checklist del día del deploy"):deploy.index("## Primer deploy, paso a paso")]
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
