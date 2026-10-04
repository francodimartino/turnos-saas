"""Setup inicial del proyecto: validación de entradas y manejo de secretos (sin base de datos)."""

import importlib.util
import secrets
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "setup_proyecto.py"


@pytest.fixture(scope="module")
def setup():
    spec = importlib.util.spec_from_file_location("setup_proyecto", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_falta_database_url_falla_con_mensaje_claro(setup, capsys):
    rc = setup.main(["--email", "franco@example.com"], env={})
    err = capsys.readouterr().err
    assert rc == 1
    assert "SETUP_DATABASE_URL" in err


def test_sin_email_ni_auth_user_id_falla(setup, capsys):
    rc = setup.main([], env={"SETUP_DATABASE_URL": "postgresql://x@127.0.0.1:1/db"})
    assert rc == 1
    assert "--email" in capsys.readouterr().err


def test_email_y_auth_user_id_son_excluyentes(setup):
    with pytest.raises(SystemExit):
        setup.main(["--email", "a@b.c", "--auth-user-id", "00000000-0000-0000-0000-000000000000"], env={})


def test_auth_user_id_invalido(setup, capsys):
    rc = setup.main(["--auth-user-id", "no-es-uuid"], env={"SETUP_DATABASE_URL": "postgresql://x@127.0.0.1:1/db"})
    assert rc == 1
    assert "UUID" in capsys.readouterr().err


def test_no_acepta_passwords_por_argumento(setup):
    with pytest.raises(SystemExit):
        setup.main(["--email", "a@b.c", "--password", "x"], env={})


def test_passwords_de_roles_desde_entorno(setup):
    valor = secrets.token_urlsafe(12)
    env = {"SETUP_PASSWORD_BOT_SERVICE": valor, "SETUP_PASSWORD_PURGE_JOB": "", "OTRA": "x"}
    assert setup.passwords_desde_entorno(env) == {"bot_service": valor}


def test_conexion_fallida_no_filtra_la_url(setup, capsys):
    secreto = secrets.token_urlsafe(12)
    rc = setup.main(
        ["--email", "a@b.c"], env={"SETUP_DATABASE_URL": f"postgresql://postgres:{secreto}@127.0.0.1:1/postgres"}
    )
    capturado = capsys.readouterr()
    assert rc == 1
    assert secreto not in capturado.out + capturado.err
