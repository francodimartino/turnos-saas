"""Configuración del enviador: se valida entera al arrancar y nunca muestra un secreto."""

import pytest
from turnos_config import ConfigInvalida

from enviador_app.config import Config

DSN = "postgresql://enviador:clave-de-prueba@h:5432/postgres"
BASE = {
    "ENVIADOR_DATABASE_URL": DSN,
    "RESEND_API_KEY": "re_clave_de_prueba",
    "EMAIL_REMITENTE": "Agendemos <avisos@agendemos.ar>",
}


def _config(**cambios):
    entorno = {**BASE, **cambios}
    return Config.desde_entorno({k: v for k, v in entorno.items() if v is not None})


def test_los_valores_por_defecto():
    c = _config()
    assert c.dsn == DSN
    assert c.sentry_dsn is None
    assert c.email.remitente == "Agendemos <avisos@agendemos.ar>"
    assert c.email.max_reintentos == 0  # los reintentos los decide la política del outbox, no el cliente de email
    assert c.base_url_telegram == "https://api.telegram.org"
    assert (c.intervalo_seg, c.espera_maxima_seg, c.concurrencia, c.lote) == (2.0, 10.0, 5, 10)
    assert (c.arriendo_seg, c.max_intentos, c.plazo_fila_seg, c.plazo_apagado_seg) == (300, 5, 120.0, 20.0)


def test_acepta_el_usuario_del_pooler_de_supabase():
    c = _config(ENVIADOR_DATABASE_URL="postgresql://enviador.abcdef:x@h:6543/postgres")
    assert c.dsn.startswith("postgresql://enviador.abcdef:")


@pytest.mark.parametrize("variable", ["ENVIADOR_DATABASE_URL", "RESEND_API_KEY", "EMAIL_REMITENTE"])
def test_falta_una_variable_obligatoria(variable):
    with pytest.raises(ConfigInvalida, match=variable):
        _config(**{variable: None})


@pytest.mark.parametrize("usuario", ["postgres", "scheduler_job", "bot_service", "enviadores"])
def test_rechaza_una_conexion_que_no_es_del_rol_enviador(usuario):
    with pytest.raises(ConfigInvalida, match="enviador"):
        _config(ENVIADOR_DATABASE_URL=f"postgresql://{usuario}:x@h/postgres")


def test_rechaza_un_remitente_de_otro_dominio():
    with pytest.raises(ConfigInvalida, match="EMAIL_REMITENTE"):
        _config(EMAIL_REMITENTE="avisos@otro.com")


def test_rechaza_una_base_de_telegram_que_no_es_local():
    with pytest.raises(ConfigInvalida, match="TELEGRAM_API_BASE_URL"):
        _config(TELEGRAM_API_BASE_URL="https://ajeno.example.com")


def test_acepta_el_simulador_local_de_telegram():
    assert _config(TELEGRAM_API_BASE_URL="http://localhost:8081/").base_url_telegram == "http://localhost:8081"


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("ENVIADOR_INTERVALO_SEG", "0"),
        ("ENVIADOR_INTERVALO_SEG", "mucho"),
        ("ENVIADOR_INTERVALO_SEG", "61"),
        ("ENVIADOR_ESPERA_MAXIMA_SEG", "1"),  # menor que el intervalo
        ("ENVIADOR_ESPERA_MAXIMA_SEG", "301"),
        ("ENVIADOR_CONCURRENCIA", "0"),
        ("ENVIADOR_CONCURRENCIA", "51"),
        ("ENVIADOR_LOTE", "501"),
        ("ENVIADOR_PLAZO_FILA_SEG", "5"),
        ("ENVIADOR_PLAZO_APAGADO_SEG", "0.5"),
        ("ENVIADOR_PLAZO_APAGADO_SEG", "121"),
        ("OUTBOX_ARRIENDO_SEG", "29"),
        ("OUTBOX_ARRIENDO_SEG", "86401"),
        ("OUTBOX_MAX_INTENTOS", "1"),
        ("OUTBOX_MAX_INTENTOS", "21"),
    ],
)
def test_un_valor_fuera_de_rango_impide_arrancar(variable, valor):
    with pytest.raises(ConfigInvalida, match=variable):
        _config(**{variable: valor})


def test_acepta_los_valores_configurados():
    c = _config(
        ENVIADOR_INTERVALO_SEG="0.5",
        ENVIADOR_ESPERA_MAXIMA_SEG="30",
        ENVIADOR_CONCURRENCIA="8",
        ENVIADOR_LOTE="16",
        OUTBOX_ARRIENDO_SEG="900",
        OUTBOX_MAX_INTENTOS="3",
        SENTRY_DSN="https://clave@o1.ingest.sentry.io/2",
    )
    assert (c.intervalo_seg, c.espera_maxima_seg, c.concurrencia, c.lote) == (0.5, 30.0, 8, 16)
    assert (c.arriendo_seg, c.max_intentos) == (900, 3)
    assert c.sentry_dsn == "https://clave@o1.ingest.sentry.io/2"


def test_el_arriendo_tiene_que_cubrir_el_peor_caso_del_lote():
    # 3 tandas de 120 s (lote 15 con concurrencia 5) no entran en un arriendo de 300 s: otra instancia reenviaría
    with pytest.raises(ConfigInvalida, match="OUTBOX_ARRIENDO_SEG"):
        _config(ENVIADOR_LOTE="15", OUTBOX_ARRIENDO_SEG="300")
    _config(ENVIADOR_LOTE="15", OUTBOX_ARRIENDO_SEG="400")


def test_la_representacion_no_muestra_secretos():
    texto = repr(_config(SENTRY_DSN="https://clave@o1.ingest.sentry.io/2"))
    assert "clave-de-prueba" not in texto and "re_clave_de_prueba" not in texto and "clave@o1" not in texto
