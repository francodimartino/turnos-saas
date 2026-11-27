"""Email y canales de los clientes en /clientes, sin base real.

Datos ficticios. Cubre normalización, topes, duplicado (409), permisos, aislamiento entre tenants y que el
identificador de los canales nunca salga.
"""

import json
import uuid

import pytest

from dashboard_backend.clientes import ErrorEmailInvalido, normalizar_email
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_clientes import app_clientes

TENANT = uuid.uuid4()
OTRO_TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
TEL = "+54 9 11 5550-0101"


def _h(escribe=False):
    h = {"Cookie": "dash_acceso=" + token(user_id=STAFF, tenant_id=TENANT)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _cliente(conn, **kw):
    kw.setdefault("tenant_id", TENANT)
    fila = conn._fila(**kw)
    conn.clientes[fila["id"]] = fila
    return fila


# --- normalización y validación (lógica pura) ----------------------------------------------------------------


def test_normalizar_email_recorta_y_pasa_a_minusculas():
    assert normalizar_email("  Ana.Perez@Correo.COM ") == "ana.perez@correo.com"


@pytest.mark.parametrize(
    "valor",
    [
        "",
        "   ",
        "sin-arroba",
        "a@b",
        "@b.com",
        "a@.com",
        "a b@c.com",
        "a@b@c.com",
        "ñandú@correo.com",
        "a@b.c\nd",
        "x" * 250 + "@a.com",
    ],
)
def test_normalizar_email_rechaza_lo_que_no_es_un_email(valor):
    with pytest.raises(ErrorEmailInvalido):
        normalizar_email(valor)


# --- alta ----------------------------------------------------------------------------------------------------


async def test_alta_con_email_lo_guarda_normalizado_y_lo_devuelve():
    app, conn, _ = app_clientes()
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": "  Ana@Correo.COM "}, headers=_h(escribe=True)
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "ana@correo.com"
    assert r.json()["canales"] == []
    insert = next(a for s, a in conn.ejecutado if s.lstrip().startswith("insert into public.end_customers"))
    assert insert[0] == TENANT and insert[4] == "ana@correo.com"


async def test_alta_sin_email_sigue_funcionando_con_email_nulo():
    app, _, _ = app_clientes()
    r = await http(app).post("/clientes", json={"phone": TEL, "name": "Cliente"}, headers=_h(escribe=True))
    assert r.status_code == 201
    assert r.json()["email"] is None


async def test_alta_con_email_de_una_ficha_existente_es_409_cliente_existente_con_su_id():
    app, conn, _ = app_clientes()
    existente = _cliente(conn, email="ana@correo.com")
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": "ANA@correo.com"}, headers=_h(escribe=True)
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "codigo": "cliente_existente",
        "mensaje": "Ya existe un cliente con ese email",
        "cliente_id": str(existente["id"]),
    }
    assert not conn.sentencias_que_contienen("insert into public.end_customers")


async def test_el_mismo_email_en_otro_tenant_no_es_duplicado():
    app, conn, _ = app_clientes()
    _cliente(conn, tenant_id=OTRO_TENANT, email="ana@correo.com")
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": "ana@correo.com"}, headers=_h(escribe=True)
    )
    assert r.status_code == 201


async def test_carrera_con_el_indice_unico_es_409_y_no_500():
    app, conn, _ = app_clientes()
    conn.choque_de_indice = uuid.uuid4()
    # El chequeo previo no la vio (la otra transacción todavía no había confirmado): solo el índice la frena.
    previo = conn.fetchval

    async def sin_ver(sql, *args):
        if "lower(email)" in sql:
            conn.ejecutado.append((sql, args))
            return None if not conn.sentencias_que_contienen("savepoint") else conn.choque_de_indice
        return await previo(sql, *args)

    conn.fetchval = sin_ver
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": "ana@correo.com"}, headers=_h(escribe=True)
    )
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cliente_existente"
    assert r.json()["detail"]["cliente_id"] == str(conn.choque_de_indice)
    assert any(s.startswith("rollback to savepoint") for s in conn.sentencias())


@pytest.mark.parametrize("email", ["no-es-email", "a@b", "", "ñandú@correo.com", "a b@c.com"])
async def test_alta_con_email_invalido_es_422_sin_repetir_el_valor(email):
    app, conn, _ = app_clientes()
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": email}, headers=_h(escribe=True)
    )
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "email_invalido"
    assert email == "" or email not in r.text
    assert not conn.sentencias_que_contienen("insert into public.end_customers")


async def test_alta_con_email_demasiado_largo_es_422_por_el_tope():
    app, conn, _ = app_clientes()
    largo = "a" * 250 + "@correo.com"
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": largo}, headers=_h(escribe=True)
    )
    assert r.status_code == 422
    assert not conn.sentencias_que_contienen("insert into public.end_customers")


# --- edición -------------------------------------------------------------------------------------------------


async def test_editar_el_email_lo_normaliza_y_audita_solo_el_nombre_del_campo():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, phone="+5491155500101", name="Cliente")
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": " Nuevo@Correo.com "}, headers=_h(escribe=True))
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "nuevo@correo.com"
    assert [(a[0], json.loads(a[3])) for a in conn.auditoria] == [("customer_updated", {"campos": ["email"]})]
    assert "nuevo@correo.com" not in json.dumps([a[3] for a in conn.auditoria])


async def test_editar_sin_enviar_email_no_lo_toca_y_enviado_en_null_lo_borra():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, phone="+5491155500101", name="Cliente", email="ana@correo.com")
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"name": "Otro"}, headers=_h(escribe=True))
    assert r.json()["email"] == "ana@correo.com"
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": None}, headers=_h(escribe=True))
    assert r.status_code == 200
    assert r.json()["email"] is None


async def test_editar_con_el_email_de_otra_ficha_es_409_cliente_existente():
    app, conn, _ = app_clientes()
    otra = _cliente(conn, email="ana@correo.com")
    fila = _cliente(conn, phone="+5491155500101", name="Cliente")
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": "ANA@correo.com"}, headers=_h(escribe=True))
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cliente_existente"
    assert r.json()["detail"]["cliente_id"] == str(otra["id"])
    assert fila["email"] is None


async def test_editar_dejando_su_propio_email_no_es_conflicto():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, phone="+5491155500101", name="Cliente", email="ana@correo.com")
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": "Ana@Correo.com"}, headers=_h(escribe=True))
    assert r.status_code == 200


async def test_editar_con_email_invalido_es_422():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, phone="+5491155500101", name="Cliente")
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": "xx"}, headers=_h(escribe=True))
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "email_invalido"


# --- permisos ------------------------------------------------------------------------------------------------


async def test_sin_manage_customers_el_alta_y_la_edicion_con_email_son_403():
    app, conn, _ = app_clientes()
    conn.estado_staff["tiene"] = False
    fila = _cliente(conn, email="ana@correo.com")
    r = await http(app).post(
        "/clientes", json={"phone": TEL, "name": "Cliente", "email": "b@correo.com"}, headers=_h(escribe=True)
    )
    assert r.status_code == 403
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"email": "c@correo.com"}, headers=_h(escribe=True))
    assert r.status_code == 403
    assert fila["email"] == "ana@correo.com"


async def test_sin_view_bookings_el_listado_y_el_detalle_no_devuelven_email_y_son_403():
    app, conn, _ = app_clientes()
    conn.estado_staff["tiene"] = False
    fila = _cliente(conn, email="ana@correo.com")
    for ruta in ("/clientes", f"/clientes/{fila['id']}"):
        r = await http(app).get(ruta, headers=_h())
        assert r.status_code == 403
        assert "ana@correo.com" not in r.text


# --- aislamiento entre tenants -------------------------------------------------------------------------------


async def test_no_se_ve_ni_se_edita_el_email_de_un_cliente_de_otro_tenant():
    app, conn, _ = app_clientes()
    ajeno = _cliente(conn, tenant_id=OTRO_TENANT, phone="+5491155500102", name="Ajeno", email="ajeno@correo.com")
    r = await http(app).get(f"/clientes/{ajeno['id']}", headers=_h())
    assert r.status_code == 404
    assert "ajeno@correo.com" not in r.text
    r = await http(app).patch(f"/clientes/{ajeno['id']}", json={"email": "nuevo@correo.com"}, headers=_h(escribe=True))
    assert r.status_code == 404
    assert ajeno["email"] == "ajeno@correo.com"
    r = await http(app).get("/clientes", headers=_h())
    assert r.status_code == 200
    assert r.json()["clientes"] == []
    assert not conn.auditoria


async def test_las_consultas_de_email_y_canales_van_filtradas_por_el_tenant_de_la_sesion():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, email="ana@correo.com")
    await http(app).get("/clientes", headers=_h())
    await http(app).get(f"/clientes/{fila['id']}", headers=_h())
    consultas = [a for s, a in conn.ejecutado if "canales_cliente" in s or "select id, email" in s]
    assert consultas and all(a[0] == TENANT for a in consultas)


# --- listado y detalle con email y canales -------------------------------------------------------------------


async def test_listado_y_detalle_devuelven_email_y_canales_sin_el_identificador():
    app, conn, _ = app_clientes()
    fila = _cliente(conn, phone="+5491155500101", name="Cliente", email="ana@correo.com")
    sin_canales = _cliente(conn, phone="+5491155500103", name="Otro")
    conn.canales = [
        {"tenant_id": TENANT, "end_customer_id": fila["id"], "canal": "telegram", "verificado": True},
        {"tenant_id": TENANT, "end_customer_id": fila["id"], "canal": "email", "verificado": False},
        {"tenant_id": OTRO_TENANT, "end_customer_id": fila["id"], "canal": "whatsapp", "verificado": True},
    ]
    esperado = [{"canal": "telegram", "verificado": True}, {"canal": "email", "verificado": False}]
    r = await http(app).get("/clientes", headers=_h())
    assert r.status_code == 200
    por_id = {c["id"]: c for c in r.json()["clientes"]}
    assert por_id[str(fila["id"])]["email"] == "ana@correo.com"
    assert por_id[str(fila["id"])]["canales"] == esperado
    assert por_id[str(sin_canales["id"])]["email"] is None
    assert por_id[str(sin_canales["id"])]["canales"] == []
    r = await http(app).get(f"/clientes/{fila['id']}", headers=_h())
    assert r.status_code == 200
    assert r.json()["email"] == "ana@correo.com"
    assert r.json()["canales"] == esperado
    assert "identificador" not in r.text


async def test_el_listado_vacio_no_consulta_emails_ni_canales():
    app, conn, _ = app_clientes()
    r = await http(app).get("/clientes", headers=_h())
    assert r.status_code == 200
    assert not conn.sentencias_que_contienen("canales_cliente")


async def test_el_email_no_aparece_en_los_logs_al_cargarlo(caplog):
    app, _, _ = app_clientes()
    with caplog.at_level(0):
        await http(app).post(
            "/clientes",
            json={"phone": TEL, "name": "Cliente", "email": "secreto.unico@correo.com"},
            headers=_h(escribe=True),
        )
    assert "secreto.unico" not in caplog.text
