"""Entorno local (`scripts/local.py`, `scripts/local_demo.py`, `tools/telegram_sim`): lógica sin Docker ni Supabase."""

import importlib.util
import re
import secrets
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from platform_notifications import TelegramAdapter

RAIZ = Path(__file__).resolve().parent.parent


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def local():
    return _cargar("local_entorno", RAIZ / "scripts" / "local.py")


@pytest.fixture(scope="module")
def demo():
    return _cargar("local_demo_entorno", RAIZ / "scripts" / "local_demo.py")


@pytest.fixture(scope="module")
def sim():
    return _cargar("telegram_sim_main", RAIZ / "tools" / "telegram_sim" / "main.py")


def _status() -> dict[str, str]:
    return {"ANON_KEY": secrets.token_hex(8), "SERVICE_ROLE_KEY": secrets.token_hex(8)}


# --- configuración de Supabase local ---


def test_config_local_usa_otros_puertos_y_otro_proyecto_pero_conserva_el_auth_hook(local):
    texto = local.config_supabase_local((RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8"))
    assert 'project_id = "turnos-saas-local"' in texto
    for puerto in (54321, 54322, 54323, 54320):  # los de la CLI por defecto: no se pisa a otros proyectos
        assert str(puerto) not in texto
    assert f"port = {local.P_SUPABASE_API}" in texto and f"shadow_port = {local.P_SUPABASE_SHADOW}" in texto
    assert "pg-functions://postgres/public/custom_access_token_hook" in texto  # igual que en el CI
    assert "[auth.mfa.totp]" in texto and "[inbucket]" in texto and "[auth.email.template.invite]" in texto
    assert texto.count("[inbucket]") == 1 and local.config_supabase_local(texto).count("[inbucket]") == 1


def test_config_del_repo_versiona_la_politica_de_auth():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+ (el CI usa 3.12)

    cfg = tomllib.loads((RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8"))
    auth = cfg["auth"]
    assert auth["enable_signup"] is False
    assert auth["minimum_password_length"] >= 12
    assert auth["password_requirements"] == "lower_upper_letters_digits_symbols"
    assert auth["enable_refresh_token_rotation"] is True and auth["refresh_token_reuse_interval"] >= 0
    assert auth["mfa"]["totp"] == {"enroll_enabled": True, "verify_enabled": True}
    assert auth["hook"]["custom_access_token"]["enabled"] is True
    invite = auth["email"]["template"]["invite"]
    assert invite["content_path"] == "./supabase/templates/invite.html"
    html = (RAIZ / "supabase" / "templates" / "invite.html").read_text(encoding="utf-8")
    assert "{{ .TokenHash }}" in html and "/invitacion" in html


def test_config_local_no_duplica_secciones_y_usa_la_plantilla_del_repo(local):
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+ (el CI usa 3.12)

    original = (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8")
    texto = local.config_supabase_local(original)
    tomllib.loads(texto)  # TOML válido: una sección duplicada lo rompería
    for seccion in ("auth.mfa.totp", "auth.email.template.invite"):
        assert texto.count(f"[{seccion}]") == 1


def test_status_env_se_parsea(local):
    salida = 'WARN: algo\nANON_KEY="abc.def"\nAPI_URL="http://127.0.0.1:55421"\nVACIA=""\n'
    assert local.parsear_status_env(salida) == {"ANON_KEY": "abc.def", "API_URL": "http://127.0.0.1:55421", "VACIA": ""}


# --- .env.local ---


def test_env_tiene_un_rol_por_servicio_con_su_propia_contrasena(local):
    env = local.generar_env(_status())
    contrasenas = {}
    for variable, rol in local.ROLES.items():
        assert env[variable].startswith(f"postgresql://{rol}:")
        assert env[variable].endswith(f"@host.docker.internal:{local.P_SUPABASE_DB}/postgres")
        assert env[f"SETUP_PASSWORD_{rol.upper()}"] == env[f"PASSWORD_{rol.upper()}"]
        contrasenas[rol] = env[f"PASSWORD_{rol.upper()}"]
    assert len(set(contrasenas.values())) == 6  # contraseñas distintas


def test_env_es_idempotente_conserva_contrasenas_y_secretos(local):
    estado = _status()
    primero = local.generar_env(estado)
    segundo = local.generar_env(estado, local.leer_env(local.formatear_env(primero)))
    assert primero == segundo


def test_la_contrasena_de_demo_se_genera_al_azar_y_se_conserva(local):
    a, b = local.generar_env(_status()), local.generar_env(_status())
    assert a["DEMO_PASSWORD"] != b["DEMO_PASSWORD"] and len(a["DEMO_PASSWORD"]) >= 12
    assert local.generar_env(_status(), a)["DEMO_PASSWORD"] == a["DEMO_PASSWORD"]


def test_la_contrasena_de_demo_cumple_la_politica_de_auth_siempre(local):
    for _ in range(500):
        c = local.generar_contrasena_demo()
        assert len(c) >= 12
        assert any(x.islower() for x in c) and any(x.isupper() for x in c)
        assert any(x.isdigit() for x in c) and any(x in "!@%^*_+-=" for x in c)
        assert not any(x in "$#'\" " for x in c)


def test_env_origenes_cookies_y_jwt_son_de_localhost(local):
    env = local.generar_env(_status())
    assert env["DASHBOARD_ORIGIN"] == "http://localhost:5173" and env["ADMIN_ORIGIN"] == "http://localhost:5174"
    assert env["VITE_API_URL_DASHBOARD"] == f"http://localhost:{local.P_DASHBOARD_BACKEND}"
    assert env["COOKIE_SECURE"] == "false" and env["COOKIE_SAMESITE"] == "strict"
    assert env["SUPABASE_URL"].startswith("http://localhost:")  # los backends exigen loopback para http
    assert env["SUPABASE_JWKS_URL"].startswith(env["SUPABASE_URL"]) and "SUPABASE_JWT_SECRET" not in env


def test_env_telegram_apunta_al_simulador_salvo_que_se_deje_vacio(local):
    assert local.generar_env(_status())["TELEGRAM_API_BASE_URL"] == "http://telegram-sim:8120"
    real = local.generar_env(_status(), {"TELEGRAM_API_BASE_URL": ""})
    assert real["TELEGRAM_API_BASE_URL"] == ""  # vacío = Telegram real (con túnel)


def test_env_no_tiene_valores_que_rompan_el_formato_de_compose(local):
    for clave, valor in local.generar_env(_status()).items():
        assert "\n" not in valor and not valor.startswith(("'", '"')), clave
        assert " #" not in valor, clave


def test_el_compose_solo_usa_variables_que_el_env_genera(local):
    env = local.generar_env(_status())
    texto = (RAIZ / "local" / "docker-compose.yml").read_text(encoding="utf-8")
    usadas = set(re.findall(r"\$\{(\w+)\}", texto))
    assert usadas and usadas <= set(env), usadas - set(env)
    servicios = yaml.safe_load(texto)["services"]
    esperados = {"bot", "dashboard-backend", "admin-backend", "job-vencimiento", "job-purga", "telegram-sim"}
    assert esperados | {"dashboard-frontend", "admin-frontend", "tools", "python-base"} <= set(servicios)
    # mínimo privilegio también en local: la service_role no llega al bot ni al job de vencimiento
    for servicio in ("bot", "job-vencimiento"):
        assert "SUPABASE_SERVICE_ROLE_KEY" not in servicios[servicio]["environment"]


def test_el_compose_publica_todos_los_puertos_solo_en_loopback():
    compose = yaml.safe_load((RAIZ / "local" / "docker-compose.yml").read_text(encoding="utf-8"))
    publicados = 0
    for nombre, servicio in compose["services"].items():
        for puerto in servicio.get("ports", []):
            publicados += 1
            assert str(puerto).startswith("127.0.0.1:"), f"{nombre} publica {puerto} fuera de 127.0.0.1"
    assert publicados >= 6


def test_puertos_publicos_detecta_solo_los_bindings_abiertos(local):
    salida = "\n".join(
        [
            "127.0.0.1:8110->8000/tcp",
            "0.0.0.0:55422->5432/tcp, [::]:55422->5432/tcp",
            "0.0.0.0:8111->8000/tcp",
            "5173/tcp",
        ]
    )
    assert local.puertos_publicos(salida) == {55422, 8111}
    assert local.puertos_publicos("127.0.0.1:8110->8000/tcp") == set()


def test_las_raices_salen_de_pyproject(local):
    assert "apps/bot" in local.raices_python() and "platform/packages/platform_tenancy" in local.raices_python()


def test_la_imagen_local_instala_desde_el_lock_con_hashes():
    dockerfile = (RAIZ / "local" / "Dockerfile.python").read_text(encoding="utf-8")
    assert "COPY requirements-dev.lock" in dockerfile
    assert "--require-hashes" in dockerfile and "-r /tmp/requirements-dev.lock" in dockerfile
    assert "DEPENDENCIAS" not in dockerfile
    build = yaml.safe_load((RAIZ / "local" / "docker-compose.yml").read_text(encoding="utf-8"))["services"][
        "python-base"
    ]["build"]
    assert build["context"] == ".." and build["dockerfile"] == "local/Dockerfile.python"
    assert "args" not in build
    assert (RAIZ / "local" / build["context"] / "requirements-dev.lock").is_file()
    ignore = (RAIZ / "local" / "Dockerfile.python.dockerignore").read_text(encoding="utf-8").split()
    assert ignore == ["*", "!requirements-dev.lock"]
    local_py = (RAIZ / "scripts" / "local.py").read_text(encoding="utf-8")
    assert "LOCAL_PYTHON_DEPENDENCIAS" not in local_py


def test_local_no_tiene_plantilla_de_respaldo_y_falla_claro_si_falta(local, tmp_path, monkeypatch):
    assert not hasattr(local, "PLANTILLA_INVITACION")
    espejo = tmp_path / "repo"
    (espejo / "supabase" / "migrations").mkdir(parents=True)
    (espejo / "supabase" / "config.toml").write_text(
        (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(local, "RAIZ", espejo)
    monkeypatch.setattr(local, "DIR_SUPABASE", tmp_path / "work")
    with pytest.raises(local.ErrorLocal, match="invite.html"):
        local.preparar_workdir_supabase()


def test_el_env_local_no_se_versiona():
    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert ".env.*" in ignorados and ".local/" in ignorados


# --- datos de demo ---


def test_totp_cumple_el_vector_de_la_rfc_6238(demo):
    secreto = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    assert demo.totp(secreto, ahora=59) == "287082"
    assert demo.totp(secreto, ahora=1111111109) == "081804"
    assert demo.totp(secreto.lower(), ahora=59) == "287082"


# --- simulador de Telegram ---


USUARIO = {"id": 7101, "nombre": "Lucía Fernández", "telefono": "5491155550101"}


def test_los_updates_del_simulador_los_entiende_el_adapter_real(sim):
    texto = TelegramAdapter.parsear_update(sim.armar_update("texto", USUARIO, 5, texto="hola"))
    assert (texto.texto, texto.usuario_id, texto.chat_id) == ("hola", "7101", "7101")
    contacto = TelegramAdapter.parsear_update(sim.armar_update("contacto", USUARIO, 6))
    assert contacto.telefono_compartido == "5491155550101"  # el contacto propio: user_id == from.id
    boton = TelegramAdapter.parsear_update(sim.armar_update("boton", USUARIO, 7, dato="rsv:ok", mensaje_origen=3))
    assert boton.callback_data == "rsv:ok" and boton.callback_id
    with pytest.raises(ValueError):
        sim.armar_update("foto", USUARIO, 8)


def test_teclados_inline_y_de_contacto_se_normalizan(sim):
    inline = {"inline_keyboard": [[{"text": "Sí", "callback_data": "a"}], [{"text": "No", "callback_data": "b"}]]}
    assert sim.teclado_de(inline) == [[{"texto": "Sí", "dato": "a"}], [{"texto": "No", "dato": "b"}]]
    contacto = {"keyboard": [[{"text": "Compartir", "request_contact": True}]]}
    assert sim.teclado_de(contacto) == [[{"texto": "Compartir", "contacto": True}]]
    assert sim.teclado_de(None) == []


def _app_sim(sim, tmp_path):
    vistos = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return httpx.Response(200)

    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    almacen = sim.Almacen(tmp_path / "estado.json")
    return TestClient(sim.crear_app(almacen, "http://bot:8000", cliente)), almacen, vistos


def test_el_bot_manda_un_mensaje_y_queda_en_la_conversacion(sim, tmp_path):
    c, _, _ = _app_sim(sim, tmp_path)
    token = f"900001:{secrets.token_urlsafe(12)}"
    c.post("/api/bots", json={"nombre": "Local", "token": token, "webhook_key": "k", "webhook_secret": "s" * 32})
    botones = {"inline_keyboard": [[{"text": "Reservar", "callback_data": "reservar"}]]}
    r = c.post(f"/bot{token}/sendMessage", json={"chat_id": "7101", "text": "Hola", "reply_markup": botones})
    assert r.json()["ok"] is True
    (mensaje,) = c.get("/api/conversacion", params={"bot": 1, "usuario": 7101}).json()
    assert mensaje["de"] == "bot" and mensaje["texto"] == "Hola"
    assert mensaje["botones"] == [[{"texto": "Reservar", "dato": "reservar"}]]
    assert c.post(f"/bot{token}/answerCallbackQuery", json={"callback_query_id": "1"}).json()["ok"] is True


def test_token_desconocido_registra_un_bot_sin_webhook_y_no_se_le_puede_escribir(sim, tmp_path):
    c, _, _ = _app_sim(sim, tmp_path)
    c.post("/bot111:abc/sendMessage", json={"chat_id": 7101, "text": "x"})
    (bot,) = c.get("/api/estado").json()["bots"]
    assert bot["enviable"] is False
    r = c.post("/api/enviar", json={"bot": bot["id"], "usuario": 7101, "tipo": "texto", "texto": "hola"})
    assert r.status_code == 422


def test_enviar_manda_el_update_al_webhook_real_con_su_secret(sim, tmp_path):
    c, _, vistos = _app_sim(sim, tmp_path)
    secreto = "secreto-1" + "x" * 24
    c.post("/api/bots", json={"nombre": "L", "token": "1:a", "webhook_key": "clave-1", "webhook_secret": secreto})
    r = c.post("/api/enviar", json={"bot": 1, "usuario": 7102, "tipo": "texto", "texto": "reservar"})
    assert r.json() == {"estado": 200}
    (req,) = vistos
    assert str(req.url) == "http://bot:8000/api/bot/telegram/clave-1"
    assert req.headers["X-Telegram-Bot-Api-Secret-Token"] == secreto
    assert b'"reservar"' in req.content
    (propio,) = c.get("/api/conversacion", params={"bot": 1, "usuario": 7102}).json()
    assert propio["de"] == "usuario" and propio["texto"] == "reservar"


def test_el_estado_sobrevive_a_un_reinicio_y_los_update_id_siguen_creciendo(sim, tmp_path):
    c, almacen, _ = _app_sim(sim, tmp_path)
    c.post("/api/bots", json={"nombre": "L", "token": "1:a", "webhook_key": "k", "webhook_secret": "s" * 32})
    c.post("/api/usuarios", json={"nombre": "Nueva Persona", "telefono": "+54 9 11 5555-0199"})
    ultimo = almacen.nuevo_update_id()
    otro = sim.Almacen(tmp_path / "estado.json")
    assert [b["webhook_key"] for b in otro.bots] == ["k"]
    assert otro.usuarios[-1]["telefono"] == "5491155550199"
    assert otro.nuevo_update_id() > ultimo
    assert c.get("/").status_code == 200 and "Simulador de Telegram" in c.get("/").text
