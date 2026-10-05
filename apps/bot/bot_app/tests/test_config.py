from pathlib import Path

import pytest

from bot_app.config import ConfiguracionInvalida, dsn_desde_entorno, validar_dsn_bot, verificar_rol
from bot_app.tests.conftest import PoolFalso


def test_acepta_solo_el_rol_bot_service():
    assert validar_dsn_bot("postgresql://bot_service:x@h:5432/db")


@pytest.mark.parametrize(
    "dsn", ["postgresql://postgres:x@h/db", "postgresql://dashboard_service:x@h/db", "postgresql://h/db"]
)
def test_rechaza_otros_roles(dsn):
    with pytest.raises(ConfiguracionInvalida):
        validar_dsn_bot(dsn)


def test_dsn_desde_entorno_exige_la_variable():
    with pytest.raises(ConfiguracionInvalida):
        dsn_desde_entorno({})
    assert dsn_desde_entorno({"BOT_DATABASE_URL": "postgresql://bot_service:x@h/db"})


async def test_verificar_rol_contra_la_base():
    pool = PoolFalso(None, None)
    await verificar_rol(pool)
    pool.rol = "postgres"
    with pytest.raises(ConfiguracionInvalida):
        await verificar_rol(pool)


def test_ningun_modulo_cambia_de_rol():
    for archivo in Path(__file__).parent.parent.glob("*.py"):
        codigo = archivo.read_text(encoding="utf-8").lower()
        assert "set role" not in codigo and "set session authorization" not in codigo, archivo.name
