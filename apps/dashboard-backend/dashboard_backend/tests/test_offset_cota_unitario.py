"""Todo `offset` de los listados tiene cota superior: un valor desmedido es 422, no un 500 de la base."""

from dashboard_backend.tests.soporte import app_de_prueba

OFFSET_MAX = 2_147_483_647


def test_todo_offset_declara_un_maximo():
    esquema = app_de_prueba()[0].openapi()
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
