"""Impersonación de solo lectura: qué pool usa cada paso, el audit previo y el tenant_id explícito (sin base real)."""

import uuid

import pytest
import turnos_queries as tq

from admin_backend import rutas_impersonacion
from admin_backend.tests.soporte import PoolFalso, app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
DESDE = "2026-03-01T00:00:00Z"
HASTA = "2026-03-08T00:00:00Z"


@pytest.fixture
def escenario(monkeypatch):
    """App con dos pools falsos distintos y un registro ordenado de audit y consultas."""
    eventos: list[tuple] = []
    pool_admin, pool_ro = PoolFalso(), PoolFalso()

    async def registrar(conn, action, entity=None, entity_id=None, metadata=None, *, tenant_id=None):
        assert conn is pool_admin.conn  # el audit se escribe con el rol platform_admin, no con el de solo lectura
        eventos.append(("audit", action, entity, entity_id, metadata, tenant_id))

    monkeypatch.setattr(rutas_impersonacion, "registrar", registrar)

    def consulta(nombre, resultado):
        async def _f(conn, **kw):
            assert conn is pool_ro.conn  # las lecturas van por el pool de solo lectura
            eventos.append((nombre, kw))
            return resultado

        monkeypatch.setattr(tq, nombre, _f)

    consulta("listar_servicios", [{"id": "s1", "tenant_id": TENANT}])
    consulta("listar_profesionales", [])
    consulta("listar_clientes", [])
    consulta("listar_packs", [])
    consulta("listar_bookings", [{"id": "b1"}])
    consulta("detalle_booking", {"id": "b1"})
    app, _, _ = app_de_prueba(pool=pool_admin, pool_readonly=pool_ro)
    return app, eventos, pool_admin, pool_ro


async def test_servicios_audita_antes_de_leer_y_pasa_el_tenant_explicito(escenario):
    app, eventos, _, _ = escenario
    admin_id = uuid.uuid4()
    async with http(app) as c:
        r = await c.get(f"/tenants/{TENANT}/impersonar/servicios", headers=bearer(user_id=admin_id))
    assert r.status_code == 200 and r.json()[0]["id"] == "s1"
    assert eventos[0][:3] == ("audit", "impersonate_view", "tenants")
    assert eventos[0][3] == TENANT and eventos[0][5] == TENANT
    assert eventos[0][4] == {"vista": "servicios"}
    assert eventos[1][0] == "listar_servicios" and eventos[1][1]["tenant_id"] == TENANT


async def test_cada_vista_registra_su_audit_y_usa_el_tenant_de_la_ruta(escenario):
    app, eventos, _, _ = escenario
    rutas = {
        "bookings": f"bookings?desde={DESDE}&hasta={HASTA}&estados=confirmed&estados=pending",
        "clientes": "clientes?busqueda=ana",
        "servicios": "servicios?incluir_inactivos=true",
        "profesionales": "profesionales",
        "packs": "packs",
    }
    async with http(app) as c:
        for vista, ruta in rutas.items():
            r = await c.get(f"/tenants/{TENANT}/impersonar/{ruta}", headers=bearer())
            assert r.status_code == 200, (vista, r.text)
    audits = [e for e in eventos if e[0] == "audit"]
    assert [a[4]["vista"] for a in audits] == list(rutas)
    consultas = [e for e in eventos if e[0] != "audit"]
    assert len(consultas) == len(rutas)
    assert all(e[1]["tenant_id"] == TENANT for e in consultas)
    bookings = next(e for e in consultas if e[0] == "listar_bookings")[1]
    assert list(bookings["estados"]) == ["confirmed", "pending"]


async def test_detalle_de_booking_y_404_si_no_es_del_tenant(escenario, monkeypatch):
    app, eventos, _, _ = escenario
    booking = uuid.uuid4()
    async with http(app) as c:
        ok = await c.get(f"/tenants/{TENANT}/impersonar/bookings/{booking}", headers=bearer())

        async def nada(conn, **kw):
            return None

        monkeypatch.setattr(tq, "detalle_booking", nada)
        no = await c.get(f"/tenants/{TENANT}/impersonar/bookings/{booking}", headers=bearer())
    assert ok.status_code == 200 and no.status_code == 404
    assert no.json()["detail"]["codigo"] == "booking_no_encontrado"
    assert eventos[0][4] == {"vista": "booking", "booking_id": str(booking)}


async def test_tenant_inexistente_404_sin_audit_ni_lectura(escenario):
    app, eventos, pool_admin, _ = escenario

    async def fetchval(sql, *args):
        return None

    pool_admin.conn.fetchval = fetchval
    async with http(app) as c:
        r = await c.get(f"/tenants/{TENANT}/impersonar/servicios", headers=bearer())
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "tenant_no_encontrado"
    assert eventos == []


async def test_la_lectura_corre_en_una_transaccion_de_solo_lectura(escenario):
    app, _, _, pool_ro = escenario
    async with http(app) as c:
        await c.get(f"/tenants/{TENANT}/impersonar/servicios", headers=bearer())
    sentencias = [sql for sql, _ in pool_ro.conn.ejecutado]
    assert sentencias[0] == "BEGIN READ ONLY" and sentencias[-1] == "COMMIT"


async def test_solo_get_hay_rutas_de_escritura_en_impersonacion(escenario):
    app, _, _, _ = escenario
    async with http(app) as c:
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = await c.request(metodo, f"/tenants/{TENANT}/impersonar/servicios", headers=bearer())
            assert r.status_code in (404, 405), metodo


async def test_parametros_invalidos_422_y_limite_acotado(escenario):
    app, _, _, _ = escenario
    async with http(app) as c:
        assert (await c.get(f"/tenants/{TENANT}/impersonar/bookings", headers=bearer())).status_code == 422
        r = await c.get(f"/tenants/{TENANT}/impersonar/servicios?limite=100000", headers=bearer())
        assert r.status_code == 422


@pytest.mark.parametrize("ruta", ["servicios", "profesionales", "clientes", "packs", f"bookings/{uuid.uuid4()}"])
async def test_exige_platform_admin(escenario, ruta):
    app, eventos, _, _ = escenario
    async with http(app) as c:
        assert (await c.get(f"/tenants/{TENANT}/impersonar/{ruta}")).status_code in (401, 403)
        r = await c.get(f"/tenants/{TENANT}/impersonar/{ruta}", headers=bearer(is_platform_admin=False))
        assert r.status_code == 403
    assert eventos == []
