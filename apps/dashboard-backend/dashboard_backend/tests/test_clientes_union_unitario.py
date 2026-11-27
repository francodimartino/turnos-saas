"""POST /clientes/{id}/unir sin base real: permiso, validación, traducción de errores y respuesta.

El SQL de la unión vive en la función `unir_end_customers` (probada con pgTAP y contra Postgres real).
"""

import uuid

import httpx
import pytest

from dashboard_backend import clientes as c
from dashboard_backend.tests.soporte import BASE, PoolFalso, http
from dashboard_backend.tests.soporte_clientes import ErrorDeBase, app_clientes
from dashboard_backend.tests.test_clientes_unitario import TENANT, _h

PRINCIPAL = uuid.uuid4()
ABSORBIDA = uuid.uuid4()


def _app():
    app, conn, _ = app_clientes()
    conn.clientes[PRINCIPAL] = conn._fila(
        id=PRINCIPAL, tenant_id=TENANT, phone="+5491155500102", name="Cai", email="cai@ejemplo.test"
    )
    return app, conn


async def _unir(app, principal=PRINCIPAL, cuerpo=None, **kw):
    cuerpo = {"absorber_id": str(ABSORBIDA)} if cuerpo is None else cuerpo
    return await http(app).post(f"/clientes/{principal}/unir", json=cuerpo, headers=_h(escribe=True, **kw))


def _llamadas_a_la_union(conn):
    return [args for sql, args in conn.ejecutado if sql == c.SQL_UNIR]


async def test_unir_responde_con_la_ficha_que_queda_y_cuanto_se_movio():
    app, conn = _app()
    r = await _unir(app)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["cliente"]["id"] == str(PRINCIPAL)
    assert cuerpo["cliente"]["email"] == "cai@ejemplo.test"
    assert cuerpo["absorbida_id"] == str(ABSORBIDA)
    assert {k: cuerpo[k] for k in ("turnos", "packs", "canales", "avisos", "registros_envio")} == {
        "turnos": 2,
        "packs": 1,
        "canales": 3,
        "avisos": 1,
        "registros_envio": 4,
    }
    assert _llamadas_a_la_union(conn) == [(PRINCIPAL, ABSORBIDA)]


async def test_unir_se_hace_en_una_sola_transaccion():
    _, conn = _app()
    pool = PoolFalso()
    pool.conn = conn
    app, _, _ = app_clientes(pool=pool)
    r = await _unir(app)
    assert r.status_code == 200, r.text
    assert pool.adquisiciones == 1


async def test_la_auditoria_la_escribe_la_funcion_de_la_base_y_no_se_duplica_desde_python():
    app, conn = _app()
    await _unir(app)
    assert conn.auditoria == []


async def test_unir_una_ficha_consigo_misma_es_422_y_no_llega_a_la_base():
    app, conn = _app()
    r = await _unir(app, cuerpo={"absorber_id": str(PRINCIPAL)})
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "union_misma_ficha"
    assert _llamadas_a_la_union(conn) == []


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"absorber_id": None}, {"absorber_id": "no-es-un-uuid"}, {"absorber_id": 7}, {"absorber_id": ""}],
)
async def test_el_cuerpo_invalido_es_422_y_no_llega_a_la_base(cuerpo):
    app, conn = _app()
    r = await _unir(app, cuerpo=cuerpo)
    assert r.status_code == 422
    assert _llamadas_a_la_union(conn) == []


async def test_un_id_de_ruta_que_no_es_uuid_es_422():
    app, conn = _app()
    r = await http(app).post("/clientes/no-es-uuid/unir", json={"absorber_id": str(ABSORBIDA)}, headers=_h(True))
    assert r.status_code == 422
    assert _llamadas_a_la_union(conn) == []


async def test_un_tenant_id_en_el_cuerpo_se_descarta():
    app, conn = _app()
    r = await _unir(app, cuerpo={"absorber_id": str(ABSORBIDA), "tenant_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert _llamadas_a_la_union(conn) == [(PRINCIPAL, ABSORBIDA)]


@pytest.mark.parametrize(
    ("sqlstate", "estado", "codigo"),
    [
        ("UF001", 404, "cliente_no_encontrado"),
        ("42501", 404, "cliente_no_encontrado"),
        ("UF002", 409, "cliente_anonimizado"),
        ("UF003", 409, "canal_en_conflicto"),
        ("UF004", 422, "union_misma_ficha"),
    ],
)
async def test_cada_error_de_la_base_se_traduce_a_un_4xx_con_su_codigo(sqlstate, estado, codigo):
    app, conn = _app()
    conn.union_error = ErrorDeBase(sqlstate)
    r = await _unir(app)
    assert r.status_code == estado, r.text
    assert r.json()["detail"]["codigo"] == codigo


async def test_ficha_ajena_e_inexistente_dan_exactamente_la_misma_respuesta():
    app, conn = _app()
    conn.union_error = ErrorDeBase("UF001")
    ajena = await _unir(app)
    conn.union_error = ErrorDeBase("42501")
    sin_contexto = await _unir(app)
    assert ajena.status_code == sin_contexto.status_code == 404
    assert ajena.json() == sin_contexto.json()


async def test_los_mensajes_de_error_no_llevan_datos_de_las_fichas():
    app, conn = _app()
    conn.union_error = ErrorDeBase("UF003")
    r = await _unir(app)
    texto = r.text
    assert "ejemplo.test" not in texto
    assert "+54" not in texto


async def test_un_error_inesperado_de_la_base_no_se_disfraza_de_un_error_de_negocio():
    app, conn = _app()
    conn.union_error = ErrorDeBase("XX000")
    r = await httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE).post(
        f"/clientes/{PRINCIPAL}/unir", json={"absorber_id": str(ABSORBIDA)}, headers=_h(escribe=True)
    )
    assert r.status_code == 500
    assert "XX000" not in r.text


async def test_el_principal_inexistente_en_el_tenant_es_404_antes_de_unir():
    app, conn, _ = app_clientes()
    conn.union_error = ErrorDeBase("UF001")
    r = await _unir(app, principal=uuid.uuid4())
    assert r.status_code == 404
