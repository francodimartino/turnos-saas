"""Chequeos estáticos de la configuración de despliegue (`render.yaml`, `vercel.json`, `docs/deploy.md`).

No crean ni tocan servicios: verifican que el Blueprint sea coherente con el código del repo (entrypoints que
existen, horarios que coinciden, variables declaradas) y que no lleve valores secretos ni autodeploy.
"""

import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
BLUEPRINT = yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))
SERVICIOS = {s["name"]: s for s in BLUEPRINT["services"]}
WEB = {n: s for n, s in SERVICIOS.items() if s["type"] == "web"}
CRON = {n: s for n, s in SERVICIOS.items() if s["type"] == "cron"}

# Variables que el código exige (config.py / main.py / desde_entorno de cada app) por servicio.
OBLIGATORIAS = {
    "turnos-bot": {"BOT_DATABASE_URL"},
    "turnos-dashboard-backend": {"DASHBOARD_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "DASHBOARD_ORIGIN"},
    "turnos-admin-backend": {
        "ADMIN_DATABASE_URL",
        "ADMIN_READONLY_DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "ADMIN_ORIGIN",
    },
    "turnos-job-purga": {"PURGE_DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"},
    "turnos-job-vencimiento": {"SCHEDULER_DATABASE_URL"},
}
# Nombres que huelen a secreto: en el Blueprint nunca llevan valor (van con `sync: false`).
PATRON_SECRETO = re.compile(r"SECRET|KEY|PASSWORD|DATABASE_URL|DSN|TOKEN")
# Dependen del dominio (todavía sin definir): siempre variables a completar, nunca valores fijos.
# Y los propios del proyecto Supabase: el Blueprint no queda atado a un proyecto concreto.
DEPENDEN_DEL_DOMINIO = {
    "DASHBOARD_ORIGIN",
    "ADMIN_ORIGIN",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "SUPABASE_URL",
    "SUPABASE_JWKS_URL",
}


def _vars(servicio: dict) -> dict:
    return {v["key"]: v for v in servicio["envVars"]}


def test_servicios_esperados_con_sus_planes():
    assert set(WEB) == {"turnos-bot", "turnos-dashboard-backend", "turnos-admin-backend"}
    assert set(CRON) == {"turnos-job-purga", "turnos-job-vencimiento"}
    assert WEB["turnos-bot"]["plan"] == "starter"
    assert WEB["turnos-dashboard-backend"]["plan"] == "free"
    assert WEB["turnos-admin-backend"]["plan"] == "free"


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_sin_autodeploy(nombre):
    assert SERVICIOS[nombre]["autoDeploy"] is False


@pytest.mark.parametrize("nombre", sorted(WEB))
def test_los_web_tienen_health_check(nombre):
    assert WEB[nombre]["healthCheckPath"] == "/health"


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_ningun_secreto_ni_dato_del_dominio_lleva_valor(nombre):
    for clave, variable in _vars(SERVICIOS[nombre]).items():
        if PATRON_SECRETO.search(clave) and clave != "DB_STATEMENT_CACHE_SIZE":
            assert variable.get("sync") is False and "value" not in variable, clave
        if clave in DEPENDEN_DEL_DOMINIO:
            assert variable.get("sync") is False and "value" not in variable, clave
        # Cada variable es una de las dos cosas, nunca ambas ni ninguna.
        assert ("value" in variable) != (variable.get("sync") is False), clave


@pytest.mark.parametrize("nombre", sorted(OBLIGATORIAS))
def test_estan_declaradas_las_variables_que_el_codigo_exige(nombre):
    faltan = OBLIGATORIAS[nombre] - set(_vars(SERVICIOS[nombre]))
    assert not faltan, faltan


def test_el_service_role_no_esta_en_el_bot():
    assert "SUPABASE_SERVICE_ROLE_KEY" not in _vars(WEB["turnos-bot"])
    assert "SUPABASE_SERVICE_ROLE_KEY" not in _vars(CRON["turnos-job-vencimiento"])


@pytest.mark.parametrize("nombre", sorted(WEB))
def test_comando_de_arranque_apunta_a_un_objeto_que_existe(nombre):
    comando = WEB[nombre]["startCommand"]
    assert "$PORT" in comando and "--host 0.0.0.0" in comando
    modulo, objeto = comando.split()[1].split(":")
    assert hasattr(importlib.import_module(modulo), objeto)
    assert ("--factory" in comando) == (objeto == "crear_app_desde_entorno")


@pytest.mark.parametrize("nombre", sorted(CRON))
def test_comando_de_cron_es_un_modulo_ejecutable(nombre):
    comando = CRON[nombre]["startCommand"].split()
    assert comando[:2] == ["python", "-m"]
    importlib.import_module(comando[2] + ".__main__")


def test_purga_a_las_06_utc_coincide_con_el_monitor_de_sentry():
    from jobs_purga.proceso import CRON_POR_DEFECTO

    purga = CRON["turnos-job-purga"]
    assert purga["schedule"] == "0 6 * * *" == CRON_POR_DEFECTO
    assert _vars(purga)["PURGA_CRON"]["value"] == purga["schedule"]
    assert CRON["turnos-job-vencimiento"]["schedule"] == "*/15 * * * *"


def test_la_cache_de_statements_se_apaga_para_el_pooler():
    for nombre in ("turnos-dashboard-backend", "turnos-admin-backend"):
        assert _vars(WEB[nombre])["DB_STATEMENT_CACHE_SIZE"]["value"] == "0"


def test_la_cookie_de_sesion_es_secure_y_strict():
    for nombre in ("turnos-dashboard-backend", "turnos-admin-backend"):
        variables = _vars(WEB[nombre])
        assert variables["COOKIE_SECURE"]["value"] == "true"
        assert variables["COOKIE_SAMESITE"]["value"] == "strict"


def test_el_blueprint_se_instala_con_uvicorn():
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    assert '"uvicorn"' in pyproject


def test_toda_variable_del_blueprint_esta_documentada_en_deploy_md():
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8")
    claves = {k for s in SERVICIOS.values() for k in _vars(s)}
    assert not {k for k in claves if k not in doc}


def test_deploy_md_no_cita_el_arnes():
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8").lower()
    assert "arnes" not in doc and "arnés" not in doc


@pytest.mark.parametrize("app", ["dashboard-frontend", "admin-frontend"])
def test_vercel_json_conserva_fallback_de_spa_y_csp(app):
    config = json.loads((RAIZ / "apps" / app / "vercel.json").read_text(encoding="utf-8"))
    assert {"source": "/(.*)", "destination": "/index.html"} in config["rewrites"]
    cabeceras = {h["key"]: h["value"] for bloque in config["headers"] for h in bloque["headers"]}
    assert "frame-ancestors 'none'" in cabeceras["Content-Security-Policy"]
    assert cabeceras["X-Frame-Options"] == "DENY"
    assert config["git"]["deploymentEnabled"]["main"] is False


def test_pip_install_cubre_los_paquetes_de_arranque():
    """`pip install .` solo incluye lo que está bajo `where`: cada módulo de arranque y cada platform_* tiene que
    estar en una de esas raíces (la prueba de instalación real corre en el CI, ver docs/deploy.md)."""
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = pyproject.split("[tool.setuptools.packages.find]")[1].split("]")[0]
    raices = [RAIZ / r for r in re.findall(r'"([^"]+)"', bloque)]
    paquetes = {d.name for r in raices for d in r.iterdir() if (d / "__init__.py").is_file()}
    esperados = {"bot_app", "dashboard_backend", "admin_backend", "jobs_purga", "jobs_vencimiento", "observabilidad"}
    esperados |= {d.name for d in (RAIZ / "platform" / "packages").iterdir() if d.name.startswith("platform_")}
    assert not esperados - paquetes, esperados - paquetes


def test_uvicorn_no_expone_el_header_server_en_render_ni_en_local():
    comandos = [s["startCommand"] for s in WEB.values()]
    compose = yaml.safe_load((RAIZ / "local" / "docker-compose.yml").read_text(encoding="utf-8"))
    comandos += [s["command"] for s in compose["services"].values() if "uvicorn" in str(s.get("command", ""))]
    assert len(comandos) >= len(WEB) + 1
    for comando in comandos:
        assert "uvicorn" in comando and "--no-server-header" in comando, comando
