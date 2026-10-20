"""Las respuestas del panel, la impersonación y los visores tienen modelo declarado y su JSON no cambia."""

import datetime
import uuid
from decimal import Decimal

import platform_admin as pa
import turnos_queries as tq
from platform_admin.tenants import ResultadoAlta, ResultadoInvitacion

from admin_backend import rutas_impersonacion
from admin_backend.tests.soporte import (
    PoolFalso,
    app_de_prueba,
    bearer,
    fila_booking_detalle,
    fila_booking_lista,
    fila_cliente,
    fila_pack,
    fila_profesional,
    fila_servicio,
    http,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTRO = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
DESDE = "2026-03-01T00:00:00Z"
HASTA = "2026-03-08T00:00:00Z"


def _json(fila):
    """JSON esperado de una fila: uuid, fechas y Decimal como texto; nulos intactos."""
    res = {}
    for clave, valor in fila.items():
        if isinstance(valor, uuid.UUID):
            valor = str(valor)
        elif isinstance(valor, datetime.datetime):
            valor = valor.isoformat().replace("+00:00", "Z")
        elif isinstance(valor, Decimal):
            valor = str(valor)
        res[clave] = valor
    return res


async def test_vistas_de_impersonacion_devuelven_las_mismas_claves_tipos_y_nulos(monkeypatch):
    async def registrar(*a, **kw):
        return None

    monkeypatch.setattr(rutas_impersonacion, "registrar", registrar)
    filas = {
        "listar_bookings": [fila_booking_lista()],
        "detalle_booking": fila_booking_detalle(),
        "listar_clientes": [fila_cliente()],
        "listar_servicios": [fila_servicio()],
        "listar_profesionales": [fila_profesional()],
        "listar_packs": [fila_pack()],
    }
    for nombre, valor in filas.items():

        async def _f(conn, valor=valor, **kw):
            return valor

        monkeypatch.setattr(tq, nombre, _f)
    app, _, _ = app_de_prueba(pool=PoolFalso(), pool_readonly=PoolFalso())
    base = f"/tenants/{TENANT}/impersonar"
    pedidos = {
        "listar_bookings": f"{base}/bookings?desde={DESDE}&hasta={HASTA}",
        "detalle_booking": f"{base}/bookings/{OTRO}",
        "listar_clientes": f"{base}/clientes",
        "listar_servicios": f"{base}/servicios",
        "listar_profesionales": f"{base}/profesionales",
        "listar_packs": f"{base}/packs",
    }
    async with http(app) as c:
        for nombre, ruta in pedidos.items():
            r = await c.get(ruta, headers=bearer())
            assert r.status_code == 200, (nombre, r.text)
            valor = filas[nombre]
            esperado = [_json(f) for f in valor] if isinstance(valor, list) else _json(valor)
            assert r.json() == esperado, nombre


async def test_alta_y_reenvio_devuelven_solo_tenant_id_y_estado(monkeypatch):
    async def crear(pool, cliente, datos, email, *, owner_nombre=None, actor_id=None):
        return ResultadoAlta(TENANT, OTRO, "enviada")

    async def reenviar(pool, cliente, tenant_id, email, *, owner_nombre=None, actor_id=None):
        return ResultadoInvitacion(TENANT, "enviada")

    monkeypatch.setattr(pa, "create_tenant", crear)
    monkeypatch.setattr(pa, "reenviar_invitacion", reenviar)
    app, _, _ = app_de_prueba()
    cuerpo = {"name": "N", "slug": "n", "owner_email": "o@x.test", "owner_nombre": "Oli"}
    async with http(app) as c:
        alta = await c.post("/tenants", json=cuerpo, headers=bearer())
        reenvio = await c.post(f"/tenants/{TENANT}/reenviar-invitacion", headers=bearer())
    esperado = {"tenant_id": str(TENANT), "estado_invitacion": "enviada"}
    assert alta.status_code == 201 and alta.json() == esperado
    assert reenvio.status_code == 200 and reenvio.json() == esperado


async def test_settings_lectura_y_edicion_conservan_su_forma(monkeypatch):
    anidado = {"a": [1, "x", None, True, 1.5]}

    async def listar(pool, *, actor_id=None):
        return {"tenant_deletion_grace_period_days": 45, "otra": anidado}

    async def editar(pool, clave, valor, *, actor_id, confirmar_reduccion=False):
        return {"key": clave, "desde": 30, "hasta": valor}

    monkeypatch.setattr(pa, "listar_settings", listar)
    monkeypatch.setattr(pa, "editar_setting", editar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        lista = await c.get("/settings", headers=bearer())
        ok = await c.put("/settings/tenant_deletion_grace_period_days", json={"valor": 45}, headers=bearer())
    assert lista.json() == {"tenant_deletion_grace_period_days": 45, "otra": anidado}
    assert ok.json() == {"key": "tenant_deletion_grace_period_days", "desde": 30, "hasta": 45}


async def test_cadena_y_descarte_conservan_su_forma(monkeypatch):
    async def verificar(pool, tenant_id, *, actor_id=None):
        return pa.ResultadoCadena(tenant_id, False, OTRO, 7)

    async def descartar(pool, tenant_id, event_id, motivo, *, actor_id=None):
        return None

    monkeypatch.setattr(pa, "verificar_cadena_audit", verificar)
    monkeypatch.setattr(pa, "descartar_limpieza_calendar", descartar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        cadena = await c.get(f"/tenants/{TENANT}/audit-log/verificar-cadena", headers=bearer())
        plataforma = await c.get("/plataforma/audit-log/verificar-cadena", headers=bearer())
        desc = await c.post(
            f"/tenants/{TENANT}/limpiezas-calendar/descartar",
            json={"event_id": "ev1", "motivo": "ya no existe"},
            headers=bearer(),
        )
    assert cadena.json() == {
        "tenant_id": str(TENANT),
        "integra": False,
        "fila_invalida_id": str(OTRO),
        "fila_invalida_chain_seq": 7,
    }
    assert plataforma.json()["fila_invalida_chain_seq"] == 7
    assert desc.json() == {"tenant_id": str(TENANT), "event_id": "ev1", "estado": "descartada"}


def test_ninguna_respuesta_del_admin_es_un_objeto_libre():
    """Toda respuesta 2xx con cuerpo declara un esquema con propiedades, salvo el mapa clave -> valor de /settings."""
    app, _, _ = app_de_prueba()
    libres = []
    for ruta, metodos in app.openapi()["paths"].items():
        if ruta == "/health" or ruta.startswith("/auth/"):
            continue  # salud y sesión son de otras áreas
        for metodo, op in metodos.items():
            for codigo, resp in op["responses"].items():
                cuerpo = resp.get("content", {}).get("application/json", {}).get("schema")
                if not codigo.startswith("2") or cuerpo is None or (ruta, metodo) == ("/settings", "get"):
                    continue
                destino = cuerpo.get("items", cuerpo)
                if "$ref" not in destino and ("properties" not in destino or "additionalProperties" in destino):
                    libres.append((metodo, ruta))
    assert libres == []
