"""La configuración se lee y se valida al arrancar: lo que falta o es inseguro impide arrancar."""

import dataclasses
from pathlib import Path

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
    assert c.sentry_dsn is None and c.supabase_url is None
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


def test_la_url_del_proyecto_es_opcional_y_se_normaliza():
    assert Config.desde_entorno(entorno()).supabase_url is None
    c = Config.desde_entorno(entorno(SUPABASE_URL="https://x.supabase.co/"))
    assert c.supabase_url == "https://x.supabase.co"


@pytest.mark.parametrize(
    "url",
    [
        "http://x.supabase.co",
        "ftp://x.supabase.co",
        "https://usuario:clave@x.supabase.co",
        "https://usuario@x.supabase.co",
        "https://x.supabase.co?a=1",
        "https://x.supabase.co#f",
        "x.supabase.co",
    ],
)
def test_la_url_del_proyecto_insegura_impide_arrancar(url):
    with pytest.raises(ConfigInvalida, match="SUPABASE_URL"):
        Config.desde_entorno(entorno(SUPABASE_URL=url))


def test_el_servicio_no_lee_ninguna_variable_con_clave_de_storage():
    """Lo que `config.py` y `main.py` leen del entorno: nada con KEY, SECRET, SERVICE_ROLE ni TOKEN."""
    import ast

    leidas: set[str] = set()
    for archivo in ("config.py", "main.py"):
        ruta = Path(__file__).resolve().parent.parent / archivo
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and nodo.value.isupper():
                leidas.add(nodo.value)
    assert {"WEB_DATABASE_URL", "WEB_ORIGIN", "SUPABASE_URL"} <= leidas
    prohibidas = [v for v in leidas if any(m in v for m in ("KEY", "SECRET", "SERVICE_ROLE", "TOKEN", "PASSWORD"))]
    assert prohibidas == []
    assert not any(f.name.endswith("key") for f in dataclasses.fields(Config))


def test_una_clave_de_storage_en_el_entorno_se_ignora():
    c = Config.desde_entorno(
        entorno(SUPABASE_SERVICE_ROLE_KEY="clave-de-storage", SUPABASE_URL="https://x.supabase.co")
    )
    assert "clave-de-storage" not in repr(c) and not hasattr(c, "supabase_service_key")


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
            SENTRY_DSN="https://abc@o1.ingest.sentry.io/1",
        )
    )
    texto = repr(c)
    for secreto in ("web_reservas@127.0.0.1", "abc@o1"):
        assert secreto not in texto
