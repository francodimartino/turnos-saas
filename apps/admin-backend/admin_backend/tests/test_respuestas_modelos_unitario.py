"""Las respuestas del panel, la impersonación y los visores tienen modelo declarado y su JSON no cambia."""

import uuid

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


U = "00000000-0000-0000-0000-0000000000bb"
T = "00000000-0000-0000-0000-0000000000aa"
INI = "2026-03-02T12:00:00Z"
FIN = "2026-03-02T12:30:00Z"
CRE = "2026-01-05T09:30:00Z"
# JSON escrito a mano: lo que ve el cliente, con sus claves, tipos y nulos.
BOOKING = {
    "id": U, "tenant_id": T, "end_customer_id": U, "service_id": U, "professional_id": U,
    "start_at": INI, "end_at": FIN, "status": "pending", "deposit_status": "not_required",
    "booking_pack_id": None, "pending_expires_at": FIN, "created_at": CRE,
    "customer_name": None, "service_name": "Corte", "professional_name": "Ana",
}  # fmt: skip
ESPERADO = {
    "listar_bookings": [BOOKING],
    "detalle_booking": {
        **BOOKING, "google_calendar_event_id": None, "customer_phone": "+5491100000000",
        "service_duration_minutes": 30, "service_price": "2500.00",
    },
    "listar_clientes": [
        {"id": U, "tenant_id": T, "channel": None, "phone": None, "name": "Luz", "deleted_at": None, "created_at": CRE}
    ],
    "listar_servicios": [
        {"id": U, "tenant_id": T, "name": "Corte", "duration_minutes": 30, "price": "2500.00",
         "deposit_amount": None, "is_active": True, "created_at": CRE}
    ],
    "listar_profesionales": [
        {"id": U, "tenant_id": T, "name": "Ana", "is_active": True, "tenant_user_id": None, "created_at": CRE}
    ],
    "listar_packs": [
        {"id": U, "tenant_id": T, "end_customer_id": U, "service_id": U, "total_sessions": 5, "used_sessions": 1,
         "origin": "purchase", "payment_status": "paid", "expires_at": None, "created_at": CRE}
    ],
}  # fmt: skip


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
            assert r.json() == ESPERADO[nombre], nombre


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


async def test_el_modelo_nuevo_serializa_igual_que_la_ruta_anotada_dict_any_de_antes():
    """Las rutas de impersonación estaban anotadas `-> dict[str, Any]` y FastAPI serializaba esos valores con el
    serializador de `Any`: `Decimal` como texto ("2500.00") y fechas UTC con `Z`. (El dashboard no anotaba y usaba
    `jsonable_encoder`, que da número y `+00:00`: por eso allá el formato es otro.) Esta ruta de prueba reproduce la
    anotación anterior con los mismos valores y se compara con la salida de los modelos nuevos."""
    from typing import Any

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin_backend.esquemas_salida import BookingDetalleOut, ServicioOut

    vieja = FastAPI()

    @vieja.get("/servicio")
    async def servicio() -> dict[str, Any]:
        return fila_servicio()

    @vieja.get("/detalle")
    async def detalle() -> dict[str, Any]:
        return fila_booking_detalle()

    nueva = FastAPI()

    @nueva.get("/servicio")
    async def servicio_nuevo() -> ServicioOut:
        return fila_servicio()  # type: ignore[return-value]

    @nueva.get("/detalle")
    async def detalle_nuevo() -> BookingDetalleOut:
        return fila_booking_detalle()  # type: ignore[return-value]

    a, b = TestClient(vieja), TestClient(nueva)
    for ruta in ("/servicio", "/detalle"):
        assert a.get(ruta).json() == b.get(ruta).json(), ruta
    assert a.get("/servicio").json()["price"] == "2500.00"
    assert a.get("/servicio").json()["created_at"] == "2026-01-05T09:30:00Z"
