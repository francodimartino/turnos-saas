"""Todo `offset` de los listados tiene cota superior: un valor desmedido es 422, no un 500 de la base."""

import pytest

from dashboard_backend.tests.soporte import app_de_prueba

OFFSET_MAX = 2_147_483_647


@pytest.fixture(scope="module")
def esquema_modulo():
    """El esquema OpenAPI se arma una sola vez por modulo (armar la app completa es lo costoso)."""
    return app_de_prueba()[0].openapi()


def test_todo_offset_declara_un_maximo(esquema_modulo):
    esquema = esquema_modulo
    encontrados = [
        (ruta, p)
        for ruta, metodos in esquema["paths"].items()
        for operacion in metodos.values()
        for p in operacion.get("parameters", [])
        if p["name"] == "offset"
    ]
    assert encontrados, "no se encontró ningún parámetro offset"
    for ruta, p in encontrados:
        maximo = p["schema"].get("maximum")
        assert maximo is not None and maximo <= OFFSET_MAX, f"{ruta}: offset sin le="
