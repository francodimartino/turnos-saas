"""`url_publica_logo`: URL pública de un logo, sin red, y sin poder salir del bucket ni de la carpeta del negocio."""

import uuid

import pytest

from turnos_almacenamiento import ConfiguracionAlmacenamientoInvalida, ErrorAlmacenamiento, url_publica_logo

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTRO = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
RUTA = f"{TENANT}/logo-{'a' * 32}.png"


def test_arma_la_url_publica_del_bucket_de_logos():
    assert url_publica_logo("https://x.supabase.co", TENANT, RUTA) == (
        f"https://x.supabase.co/storage/v1/object/public/logos/{RUTA}"
    )


def test_la_barra_final_de_la_base_no_duplica_separadores():
    assert url_publica_logo("https://x.supabase.co/", str(TENANT), RUTA).count("//") == 1


@pytest.mark.parametrize(
    "ruta",
    [
        f"{OTRO}/logo-{'a' * 32}.png",  # logo de otro negocio
        f"{TENANT}/../{OTRO}/logo-{'a' * 32}.png",
        f"{TENANT}/logo-{'a' * 32}.png?download=1",
        f"{TENANT}/logo-{'a' * 32}.png#frag",
        f"{TENANT}/logo-{'a' * 32}.svg",
        f"{TENANT}/logo-{'a' * 32}.png\n",
        f"{TENANT}/logo-{'a' * 32}.png/../../x",
        f"{TENANT}//logo-{'a' * 32}.png",
        f"{TENANT}/logo-{'A' * 32}.png",
        f"{TENANT}/logo-{'a' * 32}.png%00",
        "https://evil.test/x.png",
        "",
        None,
        5,
    ],
)
def test_una_ruta_rara_se_rechaza(ruta):
    with pytest.raises(ErrorAlmacenamiento):
        url_publica_logo("https://x.supabase.co", TENANT, ruta)


def test_un_tenant_invalido_se_rechaza():
    with pytest.raises(ErrorAlmacenamiento):
        url_publica_logo("https://x.supabase.co", "no-es-uuid", RUTA)


@pytest.mark.parametrize(
    "base",
    [
        "",
        "ftp://x.supabase.co",
        "http://x.supabase.co",
        "https://usuario:clave@x.supabase.co",
        "https://x.supabase.co?a=1",
        "https://x.supabase.co#f",
    ],
)
def test_una_base_insegura_se_rechaza(base):
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        url_publica_logo(base, TENANT, RUTA)


def test_http_solo_hacia_loopback():
    assert url_publica_logo("http://127.0.0.1:54321", TENANT, RUTA).startswith("http://127.0.0.1:54321/storage/")


@pytest.mark.parametrize("bucket", ["", "../x", "a/b", "a?b"])
def test_un_bucket_raro_se_rechaza(bucket):
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        url_publica_logo("https://x.supabase.co", TENANT, RUTA, bucket=bucket)
