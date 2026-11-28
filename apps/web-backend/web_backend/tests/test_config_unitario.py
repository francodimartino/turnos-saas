"""La configuración se lee y se valida al arrancar: lo que falta o es inseguro impide arrancar."""

import pytest

from web_backend.config import Config, ConfigInvalida

BASE = {
    "WEB_DATABASE_URL": "postgresql://web_reservas@127.0.0.1/db",
    "WEB_ORIGIN": "https://agendemos.ar",
}


def entorno(**cambios):
    e = dict(BASE)
    e.update(cambios)
    return {k: v for k, v in e.items() if v is not None}


def test_configuracion_minima_valida():
    c = Config.desde_entorno(entorno())
    assert c.web_origin == "https://agendemos.ar"
    assert c.sentry_dsn is None and c.supabase_url is None and c.supabase_service_key == ""
    assert c.statement_cache_size is None


@pytest.mark.parametrize("variable", ["WEB_DATABASE_URL", "WEB_ORIGIN"])
def test_falta_una_variable_obligatoria_y_el_error_la_nombra(variable):
    with pytest.raises(ConfigInvalida, match=variable):
        Config.desde_entorno(entorno(**{variable: None}))


@pytest.mark.parametrize(
    "origen",
    [
        "*",
        "https://*.agendemos.ar",
        "https://agendemos.ar/ruta",
        "https://agendemos.ar?x=1",
        "agendemos.ar",
        "ftp://agendemos.ar",
        "http://agendemos.ar",  # http solo hacia una máquina local
        "https://a.test,https://b.test",
    ],
)
def test_el_origen_unico_rechaza_comodines_rutas_y_http_remoto(origen):
    with pytest.raises(ConfigInvalida, match="WEB_ORIGIN"):
        Config.desde_entorno(entorno(WEB_ORIGIN=origen))


@pytest.mark.parametrize("origen", ["http://localhost:5173", "http://127.0.0.1:5173", "https://agendemos.ar/"])
def test_el_origen_admite_http_hacia_loopback_y_se_normaliza(origen):
    c = Config.desde_entorno(entorno(WEB_ORIGIN=origen))
    assert c.web_origin == origen.rstrip("/")


def test_storage_exige_url_y_clave_juntas():
    with pytest.raises(ConfigInvalida, match="SUPABASE_SERVICE_ROLE_KEY"):
        Config.desde_entorno(entorno(SUPABASE_URL="https://x.supabase.co"))
    with pytest.raises(ConfigInvalida, match="SUPABASE_URL"):
        Config.desde_entorno(entorno(SUPABASE_SERVICE_ROLE_KEY="clave"))


def test_storage_valida_la_url():
    with pytest.raises(ConfigInvalida, match="SUPABASE_URL"):
        Config.desde_entorno(entorno(SUPABASE_URL="http://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="clave"))
    c = Config.desde_entorno(entorno(SUPABASE_URL="https://x.supabase.co/", SUPABASE_SERVICE_ROLE_KEY="clave"))
    assert c.supabase_url == "https://x.supabase.co" and c.storage_configurado


@pytest.mark.parametrize("variable", ["DB_STATEMENT_CACHE_SIZE", "HTTP_MAX_BODY_BYTES"])
@pytest.mark.parametrize("valor", ["abc", "-1", "1.5"])
def test_numeros_invalidos_impiden_arrancar(variable, valor):
    with pytest.raises(ConfigInvalida, match=variable):
        Config.desde_entorno(entorno(**{variable: valor}))


def test_limite_de_body_tiene_un_tope_chico_por_defecto_y_cero_no_vale():
    assert Config.desde_entorno(entorno()).max_body_bytes == 16384
    assert Config.desde_entorno(entorno(HTTP_MAX_BODY_BYTES="2048")).max_body_bytes == 2048
    with pytest.raises(ConfigInvalida, match="HTTP_MAX_BODY_BYTES"):
        Config.desde_entorno(entorno(HTTP_MAX_BODY_BYTES="0"))


def test_los_secretos_no_salen_en_el_repr():
    c = Config.desde_entorno(
        entorno(
            SUPABASE_URL="https://x.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="clave-de-storage",
            SENTRY_DSN="https://abc@o1.ingest.sentry.io/1",
        )
    )
    texto = repr(c)
    for secreto in ("clave-de-storage", "web_reservas@127.0.0.1", "abc@o1"):
        assert secreto not in texto
