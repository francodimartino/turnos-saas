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


def test_los_backends_con_login_declaran_ip_confiable_y_turnstile():
    """El límite de login por IP y Turnstile se configuran en el panel de Render (`sync: false`), con los nombres
    que lee el código; el bot no tiene login."""
    for nombre in ("turnos-dashboard-backend", "turnos-admin-backend"):
        variables = _vars(WEB[nombre])
        for clave in ("LOGIN_IP_HEADER", "TURNSTILE_SECRET_KEY"):
            assert variables[clave].get("sync") is False and "value" not in variables[clave], (nombre, clave)
    assert not {"LOGIN_IP_HEADER", "TURNSTILE_SECRET_KEY"} & set(_vars(WEB["turnos-bot"]))


def _seccion(doc: str, titulo: str) -> str:
    """Texto de una sección `## titulo` de deploy.md, hasta el siguiente `## `."""
    m = re.search(rf"^## {re.escape(titulo)}\n(.*?)(?=^## |\Z)", doc, re.S | re.M)
    assert m, f"falta la sección '{titulo}' en docs/deploy.md"
    return m.group(1)


def _codigo_fuente() -> str:
    """Concatena los .py (sin tests), el .ts/.tsx de los frontends y supabase/config.toml, para buscar nombres."""
    partes = []
    patrones = ("apps/**/*.py", "apps/*-frontend/src/**/*.ts*", "apps/*-frontend/.env.example", "supabase/config.toml")
    for patron in patrones:
        for ruta in RAIZ.glob(patron):
            if "node_modules" not in ruta.parts and "tests" not in ruta.parts and ".test." not in ruta.name:
                partes.append(ruta.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(partes)


def test_las_variables_de_login_y_turnstile_documentadas_existen_en_el_codigo():
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8")
    codigo = _codigo_fuente()
    esperadas = {
        "LOGIN_IP_HEADER",
        "LOGIN_PROXIES_CONFIABLES",
        "LOGIN_LIMITE_IP",
        "LOGIN_LIMITE_EMAIL",
        "LOGIN_VENTANA_SEG",
        "TURNSTILE_SECRET_KEY",
        "VITE_TURNSTILE_SITE_KEY",
        "HTTP_MAX_BODY_BYTES",
    }
    for variable in esperadas:
        assert variable in doc, f"{variable} no está en docs/deploy.md"
        assert variable in codigo, f"{variable} no existe en el código"


def test_deploy_md_variables_de_las_secciones_de_auth_y_checklist_existen_en_el_codigo():
    """Todo nombre en MAYÚSCULAS con guion bajo que mencionan 'Supabase Auth' y el checklist es una variable del
    código/Blueprint o una clave de supabase/config.toml, no un invento."""
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8")
    texto = _seccion(doc, "Supabase Auth") + _seccion(doc, "Checklist del día del deploy")
    conocidas = _codigo_fuente() + (RAIZ / "render.yaml").read_text(encoding="utf-8")
    ajenas = {"TokenHash", "SiteURL"}  # plantillas de Supabase, no variables
    for nombre in set(re.findall(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`", texto)) - ajenas:
        assert nombre in conocidas, f"`{nombre}` se menciona en deploy.md pero no existe en el código"


def test_la_seccion_supabase_auth_refleja_config_toml():
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8")
    auth = _seccion(doc, "Supabase Auth")
    config = (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8")
    assert "custom_access_token_hook" in auth and "custom_access_token_hook" in config
    assert "{{ .TokenHash }}" in auth
    plantilla = (RAIZ / "supabase" / "templates" / "invite.html").read_text(encoding="utf-8")
    assert "{{ .TokenHash }}" in plantilla and "/invitacion" in plantilla
    assert "enable_signup = false" in config
    for tema in ("MFA", "signup", "SMTP", "filtradas", "refresh"):
        assert tema.lower() in auth.lower(), tema


def test_el_checklist_del_deploy_cubre_lo_pendiente():
    doc = (RAIZ / "docs" / "deploy.md").read_text(encoding="utf-8")
    checklist = _seccion(doc, "Checklist del día del deploy")
    for texto in (
        "cf-ray",
        "onrender.com",
        "429",
        "Turnstile",
        "curl",
        "connect-src",
        "COOKIE_SECURE=true",
        "COOKIE_SAMESITE=strict",
        "LOGIN_IP_HEADER",
        "puertos",
        "SMTP",
        "sb_secret_",
        "Allow Groups",
        "/health",
    ):
        assert texto in checklist, texto


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


VARIABLE_STATE = "CALENDAR_OAUTH_STATE_SECRET"


def test_clave_del_state_de_oauth_solo_en_el_dashboard_backend():
    # Solo el dashboard inicia y completa OAuth: el bot y el job de purga no deben recibir la clave del `state`.
    con_la_variable = {n for n, s in SERVICIOS.items() if VARIABLE_STATE in _vars(s)}
    assert con_la_variable == {"turnos-dashboard-backend"}, con_la_variable


def test_clave_del_state_de_oauth_no_llega_al_bot_ni_a_la_purga_en_local():
    compose = yaml.safe_load((RAIZ / "local" / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    for servicio in ("bot", "job-purga", "admin-backend", "job-vencimiento"):
        assert VARIABLE_STATE not in compose[servicio].get("environment", {}), servicio
    assert VARIABLE_STATE in compose["dashboard-backend"]["environment"]


def _nombre_dependencia(spec: str) -> str:
    return re.split(r"[\[<>=!~; ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _fijadas(archivo: str) -> dict[str, str]:
    lock = (RAIZ / archivo).read_text(encoding="utf-8")
    return {
        m.group(1).lower().replace("_", "-"): m.group(2)
        for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==(\S+)", lock, re.M)
    }


def _hashes_por_paquete(texto: str) -> dict[str, int]:
    # Cada paquete pinneado ("nombre==version") con la cantidad de --hash=sha256 de su propio bloque.
    bloques = re.split(r"^(?=[A-Za-z0-9_.\-]+==)", texto, flags=re.M)
    cuentas = {}
    for bloque in bloques:
        m = re.match(r"([A-Za-z0-9_.\-]+)==", bloque)
        if m:
            cuentas[m.group(1).lower().replace("_", "-")] = len(re.findall(r"--hash=sha256:[0-9a-f]{64}", bloque))
    return cuentas


def _directas() -> tuple[list[str], list[str]]:
    # Sin tomllib (el Python local es 3.10): se leen las dos listas de dependencias directas con una regex.
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    runtime = re.search(r"^dependencies = \[(.*?)^\]", texto, re.M | re.S).group(1)
    dev = re.search(r"^dev = \[(.*?)^\]", texto, re.M | re.S).group(1)
    return re.findall(r'"([^"]+)"', runtime), re.findall(r'"([^"]+)"', dev)


def test_el_lock_de_runtime_fija_con_hash_cada_dependencia_directa():
    runtime, _ = _directas()
    assert len(runtime) == 7
    lock = (RAIZ / "requirements.lock").read_text(encoding="utf-8")
    assert not {_nombre_dependencia(d) for d in runtime} - set(_fijadas("requirements.lock"))
    assert set(_hashes_por_paquete(lock)) == set(_fijadas("requirements.lock"))
    sin_hash = [n for n, c in _hashes_por_paquete(lock).items() if c < 1]
    assert not sin_hash, sin_hash


def test_cada_paquete_de_ambos_locks_tiene_su_propio_hash():
    for archivo in ("requirements.lock", "requirements-dev.lock"):
        cuentas = _hashes_por_paquete((RAIZ / archivo).read_text(encoding="utf-8"))
        assert cuentas, archivo
        assert not [n for n, c in cuentas.items() if c < 1], archivo


def test_el_chequeo_de_hashes_detecta_un_paquete_sin_hash():
    # Lock de ejemplo: el conteo global alcanzaría (3 hashes >= 2 paquetes), pero "b" no tiene ninguno.
    h = "    --hash=sha256:"
    lock = "\n".join(
        [
            "a==1.0 \\",
            h + "a" * 64 + " \\",
            h + "b" * 64 + " \\",
            h + "c" * 64,
            "    # via x",
            "b==2.0",
            "    # via y",
            "",
        ]
    )
    assert _hashes_por_paquete(lock) == {"a": 3, "b": 0}


def test_tzdata_esta_en_ambos_locks_y_resuelve_buenos_aires_con_ese_paquete():
    from zoneinfo import ZoneInfo

    import tzdata  # noqa: F401  (el paquete, no la base del sistema)
    from tzdata import zoneinfo as _tz  # noqa: F401

    for archivo in ("requirements.lock", "requirements-dev.lock"):
        assert _hashes_por_paquete((RAIZ / archivo).read_text(encoding="utf-8")).get("tzdata", 0) >= 1, archivo
    runtime, _ = _directas()
    assert "tzdata" in {_nombre_dependencia(d) for d in runtime}
    # Solo con tzdata: se importa el recurso del paquete (no el tzdata del sistema operativo).
    from importlib import resources

    zona = resources.files("tzdata.zoneinfo.America.Argentina").joinpath("Buenos_Aires")
    assert zona.is_file()
    assert str(ZoneInfo("America/Argentina/Buenos_Aires")) == "America/Argentina/Buenos_Aires"


def test_el_lock_de_runtime_no_trae_herramientas_de_desarrollo():
    _, dev = _directas()
    assert len(dev) == 4
    assert not {_nombre_dependencia(d) for d in dev} & set(_fijadas("requirements.lock"))
    # Transitivas de pytest que tampoco deben llegar a producción.
    assert not {"pytest", "pluggy", "iniconfig"} & set(_fijadas("requirements.lock"))


def test_el_lock_de_dev_agrega_dev_y_conserva_las_versiones_de_runtime():
    _, dev = _directas()
    runtime_lock, dev_lock = _fijadas("requirements.lock"), _fijadas("requirements-dev.lock")
    assert "--hash=sha256:" in (RAIZ / "requirements-dev.lock").read_text(encoding="utf-8")
    assert {_nombre_dependencia(d) for d in dev} <= set(dev_lock)
    for nombre, version in runtime_lock.items():
        assert dev_lock.get(nombre) == version, nombre


def test_render_y_ci_instalan_desde_el_lock_sin_resolver_de_nuevo():
    for nombre, servicio in SERVICIOS.items():
        build = servicio["buildCommand"]
        assert "pip install -r requirements.lock" in build, nombre
        assert "requirements-dev" not in build, nombre
        assert build.rstrip().endswith("pip install --no-deps ."), nombre
    ci = yaml.safe_load((RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    for job in ("python", "integracion"):
        pasos = [p for p in ci["jobs"][job]["steps"] if "run" in p and "pip" in p["run"] and "install" in p["run"]
                 and "Instalar como en Render" not in p.get("name", "")]
        assert pasos, job
        for paso in pasos:
            assert "-r requirements-dev.lock" in paso["run"], (job, paso["run"])
            assert "--no-deps" in paso["run"], (job, paso["run"])
        assert not any("pip install ." in p["run"] or '".[dev]"' in p["run"] for p in pasos)
    render = next(p for p in ci["jobs"]["integracion"]["steps"] if "Instalar como en Render" in p.get("name", ""))
    assert "install -r requirements.lock" in render["run"] and "install --no-deps ." in render["run"]
    assert "requirements-dev" not in render["run"]


# --- CI: Actions fijadas por SHA, permisos mínimos y Dependabot --------------

WORKFLOWS = sorted((RAIZ / ".github" / "workflows").glob("*.y*ml"))


def _usos(nodo):
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave == "uses":
                yield valor
            else:
                yield from _usos(valor)
    elif isinstance(nodo, list):
        for item in nodo:
            yield from _usos(item)


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_toda_action_esta_fijada_por_sha_completo_con_la_version_en_comentario(workflow):
    texto = workflow.read_text(encoding="utf-8")
    usos = list(_usos(yaml.safe_load(texto)))
    assert usos, workflow.name
    for uso in usos:
        if uso.startswith("./"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uso), f"{uso} no está fijada por SHA de 40 hex"
    for linea in texto.splitlines():
        m = re.match(r"\s*-?\s*uses:\s*(\S+)(.*)$", linea)
        if m and not m.group(1).startswith("./"):
            assert re.search(r"#\s*v?\d", m.group(2)), f"falta el comentario con la versión: {linea.strip()}"


def test_ci_declara_permisos_de_solo_lectura_a_nivel_de_workflow():
    ci = yaml.safe_load((RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert ci["permissions"] == {"contents": "read"}


def test_dependabot_cubre_pip_actions_y_los_tres_frontends():
    config = yaml.safe_load((RAIZ / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    pares = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert pares == {
        ("pip", "/"),
        ("github-actions", "/"),
        ("npm", "/apps/dashboard-frontend"),
        ("npm", "/apps/admin-frontend"),
        ("npm", "/platform/admin-frontend-template"),
    }
    for u in config["updates"]:
        assert u["schedule"]["interval"]
