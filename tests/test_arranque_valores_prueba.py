"""Cada variable `sync: false` de `render.yaml` recibe, en `scripts/verificar_arranque.py`, un valor que la
configuración real de su servicio acepta.

Si se agrega una variable con validación (un header, una URL, un largo mínimo) y `_valor_de_prueba` no la conoce,
el paso de arranque del CI fallaría recién al levantar los servicios; este test lo detecta antes y sin base.
No levanta uvicorn ni toca la base: solo construye el entorno con `_entorno_de` y valida la Config de cada servicio.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verificar_arranque", RAIZ / "scripts" / "verificar_arranque.py")
va = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(va)

SERVICIOS = {s["name"]: s for s in yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))["services"]}
SUPABASE_URL = "http://127.0.0.1:54321"
# Mismo formato que arma `main()`: un DSN por rol de base, con el usuario que exige cada servicio.
DSNS = {var: f"postgresql://{rol}:clave@127.0.0.1:54322/postgres" for var, rol in va.ROLES.items()}


def _entorno(nombre: str) -> dict[str, str]:
    return va._entorno_de(SERVICIOS[nombre], SUPABASE_URL, DSNS, {})


def test_las_variables_conocidas_de_login_son_validas():
    assert va._valor_de_prueba("LOGIN_IP_HEADER", SUPABASE_URL, DSNS) == "CF-Connecting-IP"
    assert va._valor_de_prueba("TURNSTILE_SECRET_KEY", SUPABASE_URL, DSNS) == ""


@pytest.mark.parametrize("nombre", ["turnos-dashboard-backend", "turnos-admin-backend"])
def test_config_de_backends_acepta_el_entorno_de_prueba(nombre):
    if nombre == "turnos-dashboard-backend":
        from dashboard_backend.config import Config
    else:
        from admin_backend.config import Config
    entorno = _entorno(nombre)
    Config.desde_entorno(entorno)
    # Lo que además arma main.py con el mismo entorno.
    from platform_auth import VerificadorJWT

    VerificadorJWT.desde_entorno(entorno)


def test_cliente_auth_admin_del_admin_acepta_el_entorno():
    from platform_admin import ClienteAuthAdminHttp

    ClienteAuthAdminHttp.desde_entorno(_entorno("turnos-admin-backend"))


def test_dsn_del_bot_acepta_el_entorno():
    from bot_app.config import dsn_desde_entorno

    dsn_desde_entorno(_entorno("turnos-bot"))


@pytest.mark.parametrize("nombre", ["turnos-bot", "turnos-dashboard-backend", "turnos-job-purga"])
def test_config_google_acepta_el_entorno(nombre, monkeypatch):
    from platform_calendar.config import ConfigGoogle

    for clave, valor in _entorno(nombre).items():
        monkeypatch.setenv(clave, valor)
    # Solo el dashboard-backend inicia y completa OAuth: bot y job de purga no necesitan la clave del `state`.
    ConfigGoogle.desde_entorno(requiere_state=nombre == "turnos-dashboard-backend")


@pytest.mark.parametrize("nombre", ["turnos-bot", "turnos-job-purga"])
def test_config_google_de_bot_y_job_no_exige_la_clave_del_state(nombre, monkeypatch):
    from platform_calendar.config import ConfigGoogle

    for clave, valor in _entorno(nombre).items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.delenv("CALENDAR_OAUTH_STATE_SECRET", raising=False)
    ConfigGoogle.desde_entorno(requiere_state=False)


@pytest.mark.parametrize("nombre", ["turnos-bot", "turnos-job-purga"])
def test_entorno_de_arranque_de_bot_y_job_no_lleva_la_clave_del_state(nombre):
    assert "CALENDAR_OAUTH_STATE_SECRET" not in _entorno(nombre)
    assert "CALENDAR_OAUTH_STATE_SECRET" in _entorno("turnos-dashboard-backend")


def test_toda_variable_sync_false_tiene_valor_de_prueba():
    # Cierra el hueco: una variable nueva `sync: false` sin entrada en `_valor_de_prueba` recibiría el genérico
    # y este test obliga a decidir (y al test de arriba a validar) cuál es el valor correcto.
    genericas = set()
    for servicio in SERVICIOS.values():
        for variable in servicio.get("envVars", []):
            if variable.get("sync") is False and va._valor_de_prueba(variable["key"], SUPABASE_URL, DSNS) == (
                "valor-de-prueba-local"
            ):
                genericas.add(variable["key"])
    # Texto libre sin validación en su Config: el genérico es correcto.
    assert genericas <= {
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
    }
