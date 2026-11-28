"""Superficie pública: CORS de un solo origen, headers de seguridad, topes de entrada y errores siempre con código."""

import asyncio

import asyncpg
import pytest

from web_backend.entrada import MAX_BYTES_URL
from web_backend.tests.soporte import (
    ORIGEN,
    PROFESIONAL_1,
    SERVICIO_1,
    PoolFalso,
    app_de_prueba,
    config_prueba,
    http,
)

HEADERS_DE_SEGURIDAD = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def _con_los_headers_de_seguridad(r):
    for nombre, valor in HEADERS_DE_SEGURIDAD.items():
        assert r.headers.get(nombre) == valor, nombre
    assert "server" not in r.headers


# --- CORS: un único origen --------------------------------------------------------------------------------------


async def test_el_origen_de_la_web_recibe_su_cabecera_cors():
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/sol", headers={"Origin": ORIGEN})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGEN
    assert "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize(
    "origen",
    [
        "https://otro.ejemplo.test",
        "https://agendemos.ar.evil.test",
        "http://agendemos.ar",
        "https://www.agendemos.ar",
        "null",
    ],
)
async def test_un_origen_distinto_no_recibe_cors(origen):
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/sol", headers={"Origin": origen})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("origen", ["https://otro.ejemplo.test", "null"])
async def test_el_preflight_de_otro_origen_se_rechaza(origen):
    app, _ = app_de_prueba()
    r = await http(app).options("/negocios/sol", headers={"Origin": origen, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


async def test_el_preflight_del_origen_permitido_solo_admite_get():
    app, _ = app_de_prueba()
    cliente = http(app)
    ok = await cliente.options("/negocios/sol", headers={"Origin": ORIGEN, "Access-Control-Request-Method": "GET"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGEN
    assert ok.headers["access-control-allow-methods"] == "GET"
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        r = await cliente.options("/negocios/sol", headers={"Origin": ORIGEN, "Access-Control-Request-Method": metodo})
        assert r.status_code == 400


async def test_nunca_hay_comodin_en_cors():
    app, _ = app_de_prueba()
    for origen in (ORIGEN, "https://otro.ejemplo.test"):
        r = await http(app).get("/negocios/sol", headers={"Origin": origen})
        assert r.headers.get("access-control-allow-origin") != "*"


# --- Headers de seguridad, docs apagados -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ruta",
    [
        "/negocios/sol",  # 200
        "/negocios/no-existe",  # 404 del dominio
        "/inexistente",  # 404 de enrutamiento
        "/negocios/sol/huecos",  # 422
        "/health",
    ],
)
async def test_toda_respuesta_lleva_los_headers_de_seguridad(ruta):
    app, _ = app_de_prueba()
    r = await http(app).get(ruta)
    _con_los_headers_de_seguridad(r)


@pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
async def test_docs_y_openapi_estan_apagados(ruta):
    app, _ = app_de_prueba()
    r = await http(app).get(ruta)
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "no_encontrado"
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


# --- /health ----------------------------------------------------------------------------------------------------


async def test_health_200_con_la_base_viva():
    app, _ = app_de_prueba()
    r = await http(app).get("/health")
    assert r.status_code == 200 and r.json() == {"estado": "ok"}


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError("detalle interno: host 10.0.0.5"), asyncpg.PostgresConnectionError("detalle interno")],
)
async def test_health_503_sin_detalles_si_la_base_no_contesta(error):
    app, pool = app_de_prueba()
    pool.conn.error = error
    r = await http(app).get("/health")
    assert r.status_code == 503 and r.json() == {"estado": "no_disponible"}
    assert "detalle interno" not in r.text


async def test_health_503_si_la_base_se_cuelga(monkeypatch):
    monkeypatch.setattr("web_backend.app.TIMEOUT_HEALTH_SEG", 0.05)
    app, pool = app_de_prueba()

    async def colgada(*_a, **_k):
        await asyncio.sleep(5)

    pool.conn.fetchval = colgada
    r = await http(app).get("/health")
    assert r.status_code == 503


# --- Topes de entrada --------------------------------------------------------------------------------------------


async def test_un_cuerpo_sobredimensionado_se_rechaza_con_413():
    app, pool = app_de_prueba(config=config_prueba(max_body_bytes=100))
    r = await http(app).request("GET", "/negocios/sol", content=b"x" * 101)
    assert r.status_code == 413
    assert r.json()["detail"]["codigo"] == "cuerpo_demasiado_grande"
    assert pool.adquisiciones == 0
    _con_los_headers_de_seguridad(r)


async def test_el_limite_de_body_por_defecto_es_chico():
    app, _ = app_de_prueba()
    r = await http(app).request("GET", "/negocios/sol", content=b"x" * (16 * 1024 + 1))
    assert r.status_code == 413


@pytest.mark.parametrize("parte", ["ruta", "query"])
async def test_una_url_demasiado_larga_se_rechaza_con_414(parte):
    app, pool = app_de_prueba()
    relleno = "a" * (MAX_BYTES_URL + 1)
    url = f"/negocios/{relleno}" if parte == "ruta" else f"/negocios/sol/huecos?x={relleno}"
    r = await http(app).get(url)
    assert r.status_code == 414
    assert r.json()["detail"]["codigo"] == "url_demasiado_larga"
    assert relleno not in r.text and pool.adquisiciones == 0
    _con_los_headers_de_seguridad(r)


async def test_una_url_del_largo_maximo_pasa_al_resto_de_las_validaciones():
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/" + "a" * 100)
    assert r.status_code == 404  # largo permitido; el negocio no existe


# --- Métodos y rutas ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("metodo", ["POST", "PUT", "PATCH", "DELETE"])
async def test_los_metodos_que_escriben_dan_405_con_codigo(metodo):
    app, pool = app_de_prueba()
    r = await http(app).request(metodo, "/negocios/sol", json={"nombre": "x"})
    assert r.status_code == 405
    assert r.json()["detail"]["codigo"] == "metodo_no_permitido"
    assert "GET" in r.headers["allow"]
    assert pool.adquisiciones == 0


async def test_una_ruta_inexistente_da_404_con_codigo():
    app, _ = app_de_prueba()
    r = await http(app).get("/otra/cosa")
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "no_encontrado"


async def test_no_hay_ninguna_ruta_para_listar_negocios():
    app, pool = app_de_prueba()
    for ruta in ("/negocios", "/negocios/", "/tenants", "/"):
        r = await http(app).get(ruta)
        assert r.status_code in (404, 307, 308) and "Estética" not in r.text
    assert pool.adquisiciones == 0


# --- Errores de la base: siempre un código, nunca el detalle -----------------------------------------------------


ERRORES_DE_BASE = [
    ConnectionRefusedError("detalle interno: password authentication failed for user web_reservas"),
    OSError("detalle interno"),
    asyncpg.PostgresConnectionError("detalle interno"),
    asyncpg.InterfaceError("detalle interno"),
    asyncpg.InsufficientPrivilegeError("detalle interno: permission denied for function resolver_slug_tenant"),
    asyncpg.UndefinedTableError("detalle interno"),
]


@pytest.mark.parametrize("error", ERRORES_DE_BASE, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("ruta", ["/negocios/sol", "/negocios/sol/huecos"])
async def test_la_base_caida_o_sin_permiso_da_503_con_codigo(ruta, error):
    app, pool = app_de_prueba()
    pool.conn.error = error
    params = {
        "servicio_id": str(SERVICIO_1),
        "profesional_id": str(PROFESIONAL_1),
        "desde": "2026-11-02",
        "hasta": "2026-11-03",
    }
    r = await http(app).get(ruta, params=params)
    assert r.status_code == 503
    assert r.json()["detail"]["codigo"] == "base_no_disponible"
    assert r.headers["retry-after"] == "5"
    assert "detalle interno" not in r.text
    _con_los_headers_de_seguridad(r)


@pytest.mark.parametrize("error", [ConnectionRefusedError("x"), asyncpg.PostgresConnectionError("x")])
async def test_si_no_se_puede_tomar_una_conexion_tambien_es_503(error):
    pool = PoolFalso()
    pool.error_al_adquirir = error
    app, _ = app_de_prueba(pool=pool)
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "base_no_disponible"


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), asyncio.TimeoutError()],  # noqa: UP041 - en Python 3.10 son clases distintas
    ids=["builtin", "asyncio"],
)
async def test_un_vencimiento_del_pool_o_de_la_consulta_es_503_servicio_ocupado(error):
    pool = PoolFalso()
    pool.error_al_adquirir = error
    app, _ = app_de_prueba(pool=pool)
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 503
    assert r.json()["detail"]["codigo"] == "servicio_ocupado"
    assert r.headers["retry-after"] == "5"


async def test_un_error_inesperado_responde_500_generico_con_codigo_y_headers():
    pool = PoolFalso()
    pool.error_al_adquirir = RuntimeError("detalle interno inesperado")
    app, _ = app_de_prueba(pool=pool)
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 500
    assert r.json() == {"detail": {"codigo": "error_interno", "mensaje": "Error interno del servidor."}}
    _con_los_headers_de_seguridad(r)


# --- Sin permiso: un rol que no es el de la web --------------------------------------------------------------------


async def test_con_un_rol_sin_permiso_sobre_la_funcion_publica_el_servicio_responde_503_y_no_filtra():
    """Si la conexión es de un rol sin EXECUTE sobre `resolver_slug_tenant`, la base responde `permission denied`."""
    app, pool = app_de_prueba()
    pool.conn.error = asyncpg.InsufficientPrivilegeError("permission denied for function resolver_slug_tenant")
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 503
    assert "resolver_slug_tenant" not in r.text and "permission" not in r.text


# --- Errores que no reflejan lo recibido -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [
        "<script>alert(1)</script>",
        "' or 1=1 --",
        "../../etc/passwd",
        "ana@ejemplo.test",
        "%00%0d%0aSet-Cookie:x=1",
    ],
)
async def test_ningun_error_repite_el_texto_recibido(valor):
    app, _ = app_de_prueba()
    cliente = http(app)
    respuestas = [
        await cliente.get("/negocios/" + valor),
        await cliente.get("/negocios/sol/huecos", params={"servicio_id": valor, "profesional_id": valor}),
        await cliente.get("/negocios/sol/huecos", params={"servicio_id": str(SERVICIO_1), "desde": valor}),
        await cliente.get("/otra/" + valor),
    ]
    for r in respuestas:
        assert 400 <= r.status_code < 500
        assert valor not in r.text
        assert "ejemplo.test" not in r.text and "alert(1)" not in r.text and "passwd" not in r.text
        assert "set-cookie" not in r.headers


@pytest.mark.parametrize("metodo", ["GET", "HEAD"])
async def test_un_cuerpo_chunked_sin_content_length_en_una_lectura_se_rechaza_con_413(metodo):
    app, pool = app_de_prueba(config=config_prueba(max_body_bytes=100))

    async def trozos():
        for _ in range(50):
            yield b"x" * 50

    r = await http(app).request(metodo, "/negocios/sol", content=trozos())
    assert r.status_code == 413
    assert pool.adquisiciones == 0
    _con_los_headers_de_seguridad(r)
    if metodo == "GET":
        assert r.json()["detail"]["codigo"] == "cuerpo_demasiado_grande"


async def test_una_lectura_sin_cuerpo_no_se_ve_afectada_por_el_rechazo_de_chunked():
    app, _ = app_de_prueba()
    assert (await http(app).get("/negocios/sol")).status_code == 200
