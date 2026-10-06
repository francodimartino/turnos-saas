"""/staff con conexión falsa: quién puede qué, mapeo de errores y auditoría sin PII (ARCHITECTURE.md §2.4, §2.9)."""

import uuid

import pytest
from platform_auth import ErrorInvitacion

from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_staff import AuthAdminSimulado, app_staff

TENANT = uuid.uuid4()
YO = uuid.uuid4()


def _h(*, owner: bool, escribe=True):
    aal = "aal2" if owner else "aal1"
    h = {"Cookie": "dash_acceso=" + token(user_id=YO, tenant_id=TENANT, is_owner=owner, aal=aal)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _como(conn, *, owner: bool, tiene_permiso=True):
    """Estado vivo del que llama: owner o gestor (`tiene` = manage_staff)."""
    conn.estado_staff = {"is_owner": owner, "is_active": True, "tiene": tiene_permiso}


def _acciones(conn):
    return [a[0] for a in conn.auditoria]


# --- listar ---------------------------------------------------------------------------------------------------


async def test_listar_exige_manage_staff_y_no_expone_identidades():
    app, conn = app_staff()
    otro = conn.agregar(permisos=("view_bookings",))
    conn.agregar(is_owner=True, es_yo=True)
    _como(conn, owner=False, tiene_permiso=False)
    assert (await http(app).get("/staff", headers=_h(owner=False, escribe=False))).status_code == 403

    _como(conn, owner=False)
    r = await http(app).get("/staff", headers=_h(owner=False, escribe=False))
    assert r.status_code == 200, r.text
    filas = r.json()["staff"]
    assert {f["id"] for f in filas} >= {str(otro)}
    assert all("auth_user_id" not in f and "email" not in f for f in filas)
    assert {f["id"]: f["permission_keys"] for f in filas}[str(otro)] == ["view_bookings"]


# --- permisos: solo owner vivo --------------------------------------------------------------------------------


async def test_gestor_sin_owner_no_puede_cambiar_permisos_ni_darse_owner():
    app, conn = app_staff()
    yo = conn.agregar(es_yo=True, permisos=("manage_staff",))
    _como(conn, owner=False)
    r = await http(app).put(
        f"/staff/{yo}/permisos", json={"permission_keys": ["manage_staff", "edit_prices"]}, headers=_h(owner=False)
    )
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    r = await http(app).put(f"/staff/{yo}/owner", json={"is_owner": True}, headers=_h(owner=False))
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    assert conn.sentencias_de_escritura() == []
    assert conn.staff[yo]["is_owner"] is False


async def test_owner_cambia_permisos_y_queda_auditado_sin_pii():
    app, conn = app_staff()
    destino = conn.agregar(permisos=("view_bookings", "view_reports"))
    _como(conn, owner=True)
    r = await http(app).put(
        f"/staff/{destino}/permisos",
        json={"permission_keys": ["view_bookings", "edit_prices", "edit_prices"]},
        headers=_h(owner=True),
    )
    assert r.status_code == 200, r.text
    assert r.json()["permission_keys"] == ["edit_prices", "view_bookings"]
    assert _acciones(conn) == ["staff_permissions_changed"]
    _, entidad, entidad_id, metadata, _tenant = conn.auditoria[0]
    assert entidad == "tenant_user" and entidad_id == destino
    assert "email" not in metadata
    assert '"agregados": ["edit_prices"]' in metadata and '"quitados": ["view_reports"]' in metadata


async def test_cambiar_permisos_sin_cambios_no_audita_y_clave_desconocida_es_422():
    app, conn = app_staff()
    destino = conn.agregar(permisos=("view_bookings",))
    _como(conn, owner=True)
    r = await http(app).put(
        f"/staff/{destino}/permisos", json={"permission_keys": ["view_bookings"]}, headers=_h(owner=True)
    )
    assert r.status_code == 200 and conn.auditoria == []
    r = await http(app).put(
        f"/staff/{destino}/permisos", json={"permission_keys": ["inventada"]}, headers=_h(owner=True)
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "permiso_desconocido"
    assert conn.sentencias_de_escritura() == []


async def test_staff_inexistente_es_404():
    app, conn = app_staff()
    _como(conn, owner=True)
    r = await http(app).put(f"/staff/{uuid.uuid4()}/permisos", json={"permission_keys": []}, headers=_h(owner=True))
    assert r.status_code == 404


# --- owner ----------------------------------------------------------------------------------------------------


async def test_owner_da_y_quita_owner_con_auditoria():
    app, conn = app_staff()
    destino = conn.agregar()
    _como(conn, owner=True)
    r = await http(app).put(f"/staff/{destino}/owner", json={"is_owner": True}, headers=_h(owner=True))
    assert r.status_code == 200 and r.json()["is_owner"] is True
    r = await http(app).put(f"/staff/{destino}/owner", json={"is_owner": False}, headers=_h(owner=True))
    assert r.status_code == 200 and r.json()["is_owner"] is False
    assert _acciones(conn) == ["staff_owner_granted", "staff_owner_revoked"]


async def test_ultimo_owner_no_se_degrada_y_devuelve_409_sin_auditar():
    app, conn = app_staff()
    yo = conn.agregar(is_owner=True, es_yo=True)
    conn.ultimo_owner = True
    _como(conn, owner=True)
    r = await http(app).put(f"/staff/{yo}/owner", json={"is_owner": False}, headers=_h(owner=True))
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "ultimo_owner"
    assert "owner" in r.json()["detail"]["mensaje"].lower()
    assert conn.auditoria == []


async def test_no_se_puede_hacer_owner_a_alguien_desactivado():
    app, conn = app_staff()
    destino = conn.agregar(is_active=False)
    _como(conn, owner=True)
    r = await http(app).put(f"/staff/{destino}/owner", json={"is_owner": True}, headers=_h(owner=True))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "staff_inactivo"


# --- desactivar / reactivar -----------------------------------------------------------------------------------


async def test_gestor_desactiva_y_reactiva_staff_sin_borrar_nunca():
    app, conn = app_staff()
    destino = conn.agregar(permisos=("view_bookings",))
    _como(conn, owner=False)
    r = await http(app).post(f"/staff/{destino}/desactivar", headers=_h(owner=False))
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = await http(app).post(f"/staff/{destino}/reactivar", headers=_h(owner=False))
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert _acciones(conn) == ["staff_deactivated", "staff_reactivated"]
    assert not any(x.lstrip().lower().startswith("delete") for x in conn.sentencias())


async def test_desactivar_sin_manage_staff_es_403():
    app, conn = app_staff()
    destino = conn.agregar()
    _como(conn, owner=False, tiene_permiso=False)
    assert (await http(app).post(f"/staff/{destino}/desactivar", headers=_h(owner=False))).status_code == 403
    assert conn.sentencias_de_escritura() == []


async def test_gestor_no_puede_desactivar_ni_reactivar_a_un_owner():
    app, conn = app_staff()
    owner = conn.agregar(is_owner=True)
    _como(conn, owner=False)
    for accion in ("desactivar", "reactivar"):
        r = await http(app).post(f"/staff/{owner}/{accion}", headers=_h(owner=False))
        assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    assert conn.sentencias_de_escritura() == []


async def test_ultimo_owner_no_se_desactiva_409():
    app, conn = app_staff()
    yo = conn.agregar(is_owner=True, es_yo=True)
    conn.ultimo_owner = True
    _como(conn, owner=True)
    r = await http(app).post(f"/staff/{yo}/desactivar", headers=_h(owner=True))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "ultimo_owner"
    assert conn.auditoria == []


async def test_desactivar_dos_veces_es_idempotente_y_no_reaudita():
    app, conn = app_staff()
    destino = conn.agregar(is_active=False)
    _como(conn, owner=True)
    r = await http(app).post(f"/staff/{destino}/desactivar", headers=_h(owner=True))
    assert r.status_code == 200 and conn.auditoria == []


# --- invitar --------------------------------------------------------------------------------------------------


async def test_invitar_exige_owner_y_no_llama_a_la_api_sin_serlo():
    admin = AuthAdminSimulado()
    app, conn = app_staff(cliente_admin=admin)
    _como(conn, owner=False)
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "permission_keys": ["view_bookings"]},
        headers=_h(owner=False),
    )
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    assert admin.emails == []


async def test_invitar_crea_staff_y_responde_sin_email():
    admin = AuthAdminSimulado()
    app, conn = app_staff(cliente_admin=admin)
    _como(conn, owner=True)
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "Nuevo@Ejemplo.invalid", "permission_keys": ["view_bookings"]},
        headers=_h(owner=True),
    )
    assert r.status_code == 201, r.text
    assert r.json()["creado"] is True and "tenant_user_id" in r.json()
    assert "ejemplo.invalid" not in r.text
    assert admin.emails == ["nuevo@ejemplo.invalid"]
    assert _acciones(conn) == ["staff_invited"]
    assert "ejemplo.invalid" not in repr(conn.auditoria)


@pytest.mark.parametrize(
    ("cuerpo", "codigo"),
    [
        ({"email": "sin-arroba", "permission_keys": []}, "email_invalido"),
        ({"email": "a@ejemplo.invalid", "permission_keys": ["inventada"]}, "permiso_desconocido"),
    ],
)
async def test_invitar_valida_email_y_permisos(cuerpo, codigo):
    admin = AuthAdminSimulado()
    app, conn = app_staff(cliente_admin=admin)
    _como(conn, owner=True)
    r = await http(app).post("/staff/invitaciones", json=cuerpo, headers=_h(owner=True))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == codigo
    assert "sin-arroba" not in r.text
    assert admin.emails == []


@pytest.mark.parametrize(("reintentable", "estado"), [(True, 503), (False, 502)])
async def test_invitar_traduce_el_fallo_de_la_api_sin_filtrar_detalles(reintentable, estado):
    admin = AuthAdminSimulado(error=ErrorInvitacion("detalle interno", reintentable=reintentable))
    app, conn = app_staff(cliente_admin=admin)
    _como(conn, owner=True)
    r = await http(app).post(
        "/staff/invitaciones", json={"email": "a@ejemplo.invalid", "permission_keys": []}, headers=_h(owner=True)
    )
    assert r.status_code == estado
    assert r.json()["detail"]["reintentable"] is reintentable
    assert "detalle interno" not in r.text and "ejemplo.invalid" not in r.text


async def test_invitar_sin_service_role_configurada_es_503(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    app, conn = app_staff()  # sin cliente inyectado: lo arma desde el entorno, que no tiene la clave
    _como(conn, owner=True)
    r = await http(app).post(
        "/staff/invitaciones", json={"email": "a@ejemplo.invalid", "permission_keys": []}, headers=_h(owner=True)
    )
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "invitaciones_no_configuradas"
