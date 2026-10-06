import pytest

from admin_backend.config import Config, ConfigInvalida

BASE = {
    "ADMIN_DATABASE_URL": "postgresql://x@localhost/db",
    "SUPABASE_URL": "https://p.supabase.test",
    "SUPABASE_ANON_KEY": "anon",
    "ADMIN_ORIGIN": "https://admin.ejemplo.test",
    "ADMIN_READONLY_DATABASE_URL": "postgresql://ro@localhost/db",
}


def test_config_minima_con_defectos_seguros():
    c = Config.desde_entorno(BASE)
    assert c.cookie_secure is True and c.cookie_samesite == "strict"
    assert c.sentry_dsn is None


@pytest.mark.parametrize("falta", list(BASE))
def test_falta_variable_obligatoria(falta):
    entorno = {k: v for k, v in BASE.items() if k != falta}
    with pytest.raises(ConfigInvalida, match=falta):
        Config.desde_entorno(entorno)


def test_supabase_http_solo_hacia_loopback():
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "SUPABASE_URL": "http://p.supabase.test"})
    Config.desde_entorno({**BASE, "SUPABASE_URL": "http://127.0.0.1:54321"})


def test_origen_con_ruta_o_comodin_se_rechaza():
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "ADMIN_ORIGIN": "https://admin.ejemplo.test/x"})


def test_samesite_none_no_se_admite():
    with pytest.raises(ConfigInvalida):
        Config.desde_entorno({**BASE, "COOKIE_SAMESITE": "none"})


def test_la_service_role_no_forma_parte_de_la_config():
    c = Config.desde_entorno({**BASE, "SUPABASE_SERVICE_ROLE_KEY": "secreto-service"})
    assert "secreto-service" not in repr(c)
    assert not hasattr(c, "supabase_service_role_key")


def test_url_de_solo_lectura_va_aparte_y_no_se_muestra_en_el_repr():
    c = Config.desde_entorno({**BASE, "ADMIN_READONLY_DATABASE_URL": "postgresql://ro:clave-ro@localhost/db"})
    assert c.readonly_database_url == "postgresql://ro:clave-ro@localhost/db"
    assert c.readonly_database_url != c.database_url
    assert "clave-ro" not in repr(c)
