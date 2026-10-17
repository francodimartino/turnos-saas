"""Configuración por entorno: falla al arrancar, no por request, y nunca admite la service_role."""

import pytest

from dashboard_backend.config import Config, ConfigInvalida

BASE = {
    "DASHBOARD_DATABASE_URL": "postgresql://dashboard_service:x@localhost/postgres",
    "SUPABASE_URL": "https://proyecto.supabase.co",
    "SUPABASE_ANON_KEY": "anon-de-prueba",
    "DASHBOARD_ORIGIN": "https://panel.ejemplo.test",
}


def test_desde_entorno_valido_con_defaults_seguros():
    c = Config.desde_entorno(BASE)
    assert c.cookie_secure is True
    assert c.cookie_samesite == "strict"
    assert c.dashboard_origin == "https://panel.ejemplo.test"
    assert c.sentry_dsn is None


@pytest.mark.parametrize("faltante", list(BASE))
def test_falta_una_variable_obligatoria(faltante):
    entorno = {k: v for k, v in BASE.items() if k != faltante}
    with pytest.raises(ConfigInvalida, match=faltante):
        Config.desde_entorno(entorno)


@pytest.mark.parametrize("origen", ["*", "panel.ejemplo.test", "https://panel.ejemplo.test/ruta", "ftp://x.test"])
def test_el_origen_cors_debe_ser_un_unico_origen_explicito(origen):
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "DASHBOARD_ORIGIN": origen})


def test_origen_con_barra_final_se_normaliza():
    c = Config.desde_entorno({**BASE, "DASHBOARD_ORIGIN": "https://panel.ejemplo.test/"})
    assert c.dashboard_origin == "https://panel.ejemplo.test"


def test_supabase_url_http_solo_hacia_loopback():
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "SUPABASE_URL": "http://proyecto.supabase.co"})
    Config.desde_entorno({**BASE, "SUPABASE_URL": "http://127.0.0.1:54321"})


def test_samesite_none_no_se_admite():
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "COOKIE_SAMESITE": "none"})
    assert Config.desde_entorno({**BASE, "COOKIE_SAMESITE": "Lax"}).cookie_samesite == "lax"


def test_cookie_secure_se_apaga_solo_explicitamente():
    assert Config.desde_entorno({**BASE, "COOKIE_SECURE": "false"}).cookie_secure is False
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "COOKIE_SECURE": "quizas"})


def test_la_config_no_tiene_ni_lee_la_service_role():
    campos = set(Config.__dataclass_fields__)
    assert not any("service" in c for c in campos)
    c = Config.desde_entorno({**BASE, "SUPABASE_SERVICE_ROLE_KEY": "no-debe-usarse"})
    assert "no-debe-usarse" not in repr(c)


def test_repr_no_expone_secretos():
    c = Config.desde_entorno(BASE)
    assert "anon-de-prueba" not in repr(c)
    assert "dashboard_service:x" not in repr(c)


def test_proteccion_de_login_defaults_y_variables():
    c = Config.desde_entorno(BASE)
    assert (c.login_limites.por_ip, c.login_limites.por_email, c.login_limites.ventana_seg) == (20, 5, 900)
    assert c.turnstile_secret == ""
    c = Config.desde_entorno(
        {**BASE, "LOGIN_LIMITE_IP": "7", "LOGIN_LIMITE_EMAIL": "3", "LOGIN_VENTANA_SEG": "60",
         "TURNSTILE_SECRET_KEY": " clave ", "LOGIN_IP_HEADER": "CF-Connecting-IP"}
    )  # fmt: skip
    assert (c.login_limites.por_ip, c.login_limites.por_email, c.login_limites.ventana_seg) == (7, 3, 60)
    assert c.turnstile_secret == "clave"
    assert "clave" not in repr(c)


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("LOGIN_LIMITE_IP", "0"),
        ("LOGIN_LIMITE_EMAIL", "0"),
        ("LOGIN_LIMITE_EMAIL", "-1"),
        ("LOGIN_VENTANA_SEG", "0"),
        ("LOGIN_LIMITE_IP", "muchos"),
        ("LOGIN_PROXIES_CONFIABLES", "0"),
        ("LOGIN_IP_HEADER", "X-Real-IP"),
    ],
)
def test_proteccion_de_login_rechaza_valores_invalidos(variable, valor):
    with pytest.raises(ConfigInvalida, match=variable):
        Config.desde_entorno({**BASE, variable: valor})
