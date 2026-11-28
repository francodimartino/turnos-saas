"""/local/avisos con conexión falsa: default por rol, permisos, topes, errores 4xx y auditoría (ARCHITECTURE.md §2.12).

El doble de la conexión imita las reglas de la base (check de WhatsApp, clave foránea compuesta, garantía de
destinatarios), así que un pedido que la base rechazaría acá también falla.
"""

import json
import uuid

import pytest
from asyncpg.exceptions import CheckViolationError, DeadlockDetectedError, ForeignKeyViolationError
from fastapi import APIRouter

from dashboard_backend import avisos_negocio as a
from dashboard_backend import rutas_avisos
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_avisos import app_avisos

TENANT = uuid.uuid4()
OTRO_TENANT = uuid.uuid4()
YO = uuid.uuid4()


def _h(*, owner=False, escribe=True, tenant=TENANT):
    h = {
        "Cookie": "dash_acceso=" + token(user_id=YO, tenant_id=tenant, is_owner=owner, aal="aal2" if owner else "aal1")
    }
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _escenario():
    """Owner, quien aprueba turnos, un empleado de solo lectura, un desactivado y una persona de otro negocio."""
    app, conn = app_avisos()
    ids = {
        "owner": conn.agregar(TENANT, is_owner=True),
        "aprobador": conn.agregar(TENANT, permisos=("approve_bookings", "view_bookings")),
        "empleado": conn.agregar(TENANT, permisos=("view_bookings",)),
        "inactivo": conn.agregar(TENANT, is_active=False, permisos=("approve_bookings",)),
        "ajeno": conn.agregar(OTRO_TENANT, is_owner=True),
    }
    return app, conn, ids


def _por_id(r):
    return {f["id"]: f for f in r.json()["staff"]}


def _cambio(staff_id, **kw):
    return {"staff_id": str(staff_id), **kw}


async def _put(app, cambios, **kw):
    return await http(app).put("/local/avisos", json={"cambios": cambios}, headers=_h(**kw))


def _auditoria(conn):
    return [(a[0], a[1], json.loads(a[3])) for a in conn.auditoria]


# --- lectura y regla por defecto --------------------------------------------------------------------------------


async def test_sin_filas_rige_el_default_owner_y_quienes_aprueban_turnos_por_email():
    app, conn, ids = _escenario()
    conn.identidades = {ids["empleado"]: {"email": "e@ejemplo.invalid", "nombre": "Ema Empleada"}}
    r = await http(app).get("/local/avisos", headers=_h(owner=True, escribe=False))
    assert r.status_code == 200, r.text
    por_id = _por_id(r)
    # solo el staff activo del tenant: ni el desactivado ni la persona de otro negocio
    assert set(por_id) == {str(ids[n]) for n in ("owner", "aprobador", "empleado")}
    for n in ("owner", "aprobador"):
        f = por_id[str(ids[n])]
        assert (f["es_default"], f["recibe_avisos"], f["canal_email"], f["recibe_por_email"]) == (
            True,
            True,
            True,
            True,
        )
    emp = por_id[str(ids["empleado"])]
    assert (emp["es_default"], emp["recibe_avisos"], emp["canal_email"], emp["recibe_por_email"]) == (
        True,
        False,
        False,
        False,
    )
    assert (emp["nombre"], emp["email"]) == ("Ema Empleada", "e@ejemplo.invalid")
    assert all(f["canal_whatsapp"] is False for f in por_id.values())
    assert {n: por_id[str(ids[n])]["is_owner"] for n in ("owner", "aprobador", "empleado")} == {
        "owner": True,
        "aprobador": False,
        "empleado": False,
    }
    assert r.json()["canales"] == [{"canal": "email", "disponible": True}, {"canal": "whatsapp", "disponible": False}]
    assert all("auth_user_id" not in f and "tenant_id" not in f for f in por_id.values())
    assert conn.escrituras_de_config() == []


async def test_una_fila_reemplaza_al_default_de_esa_persona():
    app, conn, ids = _escenario()
    conn.poner_config(ids["aprobador"], recibe=True, email=False)
    conn.poner_config(ids["empleado"], recibe=True, email=True)
    por_id = _por_id(await http(app).get("/local/avisos", headers=_h(owner=True, escribe=False)))
    apr = por_id[str(ids["aprobador"])]
    assert (apr["es_default"], apr["recibe_avisos"], apr["canal_email"], apr["recibe_por_email"]) == (
        False,
        True,
        False,
        False,
    )
    emp = por_id[str(ids["empleado"])]
    assert (emp["es_default"], emp["recibe_por_email"]) == (False, True)


async def test_es_yo_marca_solo_a_la_persona_de_la_sesion():
    app, conn, ids = _escenario()
    conn.staff[ids["owner"]]["auth_user_id"] = YO
    conn.staff[ids["aprobador"]]["auth_user_id"] = uuid.uuid4()
    por_id = _por_id(await http(app).get("/local/avisos", headers=_h(owner=True, escribe=False)))
    assert {n: por_id[str(ids[n])]["es_yo"] for n in ("owner", "aprobador", "empleado")} == {
        "owner": True,
        "aprobador": False,
        "empleado": False,
    }


@pytest.mark.parametrize("antes,despues", [((True, False), (False, True)), ((False, True), (True, False))])
async def test_cambiar_a_quien_ya_tiene_fila_con_valores_asimetricos_guarda_cada_columna_en_su_lugar(antes, despues):
    app, conn, ids = _escenario()
    conn.poner_config(ids["empleado"], recibe=antes[0], email=antes[1])
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=despues[0], canal_email=despues[1])])
    assert r.status_code == 200, r.text
    assert a.SQL_ACTUALIZAR in conn.sentencias() and a.SQL_INSERTAR not in conn.sentencias()
    assert conn.config[ids["empleado"]] == {
        "recibe_avisos": despues[0],
        "canal_email": despues[1],
        "canal_whatsapp": False,
    }
    f = _por_id(r)[str(ids["empleado"])]
    assert (f["es_default"], f["recibe_avisos"], f["canal_email"], f["recibe_por_email"]) == (
        False,
        despues[0],
        despues[1],
        despues[0] and despues[1],
    )
    [(_accion, _entidad, metadata)] = _auditoria(conn)
    assert metadata["cambios"] == [
        {
            "staff_id": str(ids["empleado"]),
            "por_defecto": False,
            "recibe_avisos": despues[0],
            "canal_email": despues[1],
        }
    ]


async def test_pedido_mixto_audita_solo_a_quienes_cambiaron():
    app, conn, ids = _escenario()
    otro = conn.agregar(TENANT, permisos=("view_bookings",))
    conn.poner_config(ids["aprobador"], recibe=True, email=False)  # el pedido le pide lo mismo: queda igual
    conn.poner_config(otro, recibe=True, email=True)  # vuelve al default: cambia
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=False, canal_email=True),  # sin fila: se crea
            _cambio(ids["aprobador"], recibe_avisos=True, canal_email=False),  # igual a lo que tiene
            _cambio(ids["owner"], por_defecto=True),  # default sin tener fila: nada que borrar
            _cambio(otro, por_defecto=True),
        ],
    )
    assert r.status_code == 200, r.text
    assert set(conn.config) == {ids["empleado"], ids["aprobador"]}
    [(_accion, _entidad, metadata)] = _auditoria(conn)
    assert sorted(c["staff_id"] for c in metadata["cambios"]) == sorted([str(ids["empleado"]), str(otro)])
    assert len(metadata["cambios"]) == 2


# --- permisos ---------------------------------------------------------------------------------------------------


async def test_quien_gestiona_el_personal_sin_ser_owner_lee_y_cambia_la_configuracion():
    app, conn, ids = _escenario()
    conn.llamador = {"is_owner": False, "permisos": {"manage_staff"}}
    assert (await http(app).get("/local/avisos", headers=_h(escribe=False))).status_code == 200
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)])
    assert r.status_code == 200, r.text
    assert conn.config[ids["empleado"]] == {"recibe_avisos": True, "canal_email": True, "canal_whatsapp": False}


@pytest.mark.parametrize(
    "permisos",
    [
        set(),
        {"approve_bookings"},
        {"view_bookings", "manage_customers", "manage_schedule", "edit_prices", "delete_customers"},
    ],
)
async def test_con_otros_permisos_pero_sin_gestionar_el_personal_es_403_y_no_se_escribe(permisos):
    app, conn, ids = _escenario()
    conn.llamador = {"is_owner": False, "permisos": permisos}
    assert (await http(app).get("/local/avisos", headers=_h(escribe=False))).status_code == 403
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)])
    assert r.status_code == 403
    assert conn.escrituras_de_config() == [] and conn.config == {} and conn.auditoria == []


async def test_el_owner_puede_aunque_no_tenga_el_permiso_asignado():
    app, conn, ids = _escenario()
    conn.llamador = {"is_owner": True, "permisos": set()}
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)], owner=True)
    assert r.status_code == 200, r.text


async def test_sin_sesion_es_401_y_sin_origen_es_403():
    app, conn, ids = _escenario()
    assert (await http(app).get("/local/avisos")).status_code == 401
    h = _h(escribe=False)
    r = await http(app).put("/local/avisos", json={"cambios": [_cambio(ids["empleado"], por_defecto=True)]}, headers=h)
    assert r.status_code == 403
    assert conn.config == {}


# --- escritura y auditoría --------------------------------------------------------------------------------------


async def test_put_guarda_audita_con_el_actor_del_contexto_y_devuelve_el_resultado():
    app, conn, ids = _escenario()
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
            _cambio(ids["aprobador"], recibe_avisos=True, canal_email=False),
        ],
    )
    assert r.status_code == 200, r.text
    por_id = _por_id(r)
    assert por_id[str(ids["empleado"])]["recibe_por_email"] is True
    assert por_id[str(ids["empleado"])]["es_default"] is False
    assert por_id[str(ids["aprobador"])]["recibe_por_email"] is False
    assert conn.config[ids["empleado"]]["canal_whatsapp"] is False
    audit = _auditoria(conn)
    assert len(audit) == 1
    accion, entidad, metadata = audit[0]
    assert (accion, entidad) == ("avisos_negocio_updated", "avisos_negocio_config")
    assert {c["staff_id"] for c in metadata["cambios"]} == {str(ids["empleado"]), str(ids["aprobador"])}
    assert all(c["por_defecto"] is False for c in metadata["cambios"])
    # la auditoría nunca lleva nombres ni emails
    assert "@" not in json.dumps(metadata) and "nombre" not in metadata
    # el actor y el tenant salen del contexto de la transacción, no del body
    contexto = [a for sql, a in conn.ejecutado if "set_config('app.current_actor_id'" in sql]
    assert contexto == [(str(TENANT), str(YO))]
    assert conn.auditoria[0][4] is None  # sin tenant_id explícito: la base usa el del contexto


async def test_volver_al_default_borra_la_fila_y_tambien_audita():
    app, conn, ids = _escenario()
    conn.poner_config(ids["empleado"], recibe=True, email=True)
    r = await _put(app, [_cambio(ids["empleado"], por_defecto=True)])
    assert r.status_code == 200, r.text
    assert ids["empleado"] not in conn.config
    f = _por_id(r)[str(ids["empleado"])]
    assert (f["es_default"], f["recibe_por_email"]) == (True, False)
    [(accion, _entidad, metadata)] = _auditoria(conn)
    assert accion == "avisos_negocio_updated"
    assert metadata["cambios"] == [
        {"staff_id": str(ids["empleado"]), "por_defecto": True, "recibe_avisos": None, "canal_email": None}
    ]


async def test_lo_que_no_cambia_nada_ni_escribe_ni_audita():
    app, conn, ids = _escenario()
    conn.poner_config(ids["empleado"], recibe=True, email=True)
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),  # ya está así
            _cambio(ids["owner"], por_defecto=True),  # ya está en el default
        ],
    )
    assert r.status_code == 200, r.text
    assert conn.escrituras_de_config() == [] and conn.auditoria == []


async def test_se_puede_intercambiar_al_destinatario_en_un_solo_pedido_sin_importar_el_orden():
    app, conn, ids = _escenario()
    # el owner es el único que recibe por defecto (el aprobador queda fuera); se cambia de uno al otro
    conn.poner_config(ids["aprobador"], recibe=False, email=False)
    r = await _put(
        app,
        [
            _cambio(ids["owner"], recibe_avisos=False, canal_email=True),
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
        ],
    )
    assert r.status_code == 200, r.text
    por_id = _por_id(r)
    assert [i for i, f in por_id.items() if f["recibe_por_email"]] == [str(ids["empleado"])]


# --- no dejar al negocio sin destinatario ----------------------------------------------------------------------


async def test_dejar_al_negocio_sin_destinatario_de_email_es_409_y_no_cambia_nada():
    app, conn, ids = _escenario()
    conn.poner_config(ids["aprobador"], recibe=False, email=True)
    antes = dict(conn.config)
    # el owner (único destinatario) deja de recibir
    r = await _put(app, [_cambio(ids["owner"], recibe_avisos=False, canal_email=True)])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert "mensaje" in r.json()["detail"]
    assert conn.config == antes and conn.auditoria == []
    # lo mismo si lo que se saca es el canal de email
    r = await _put(app, [_cambio(ids["owner"], recibe_avisos=True, canal_email=False)])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert conn.config == antes and conn.auditoria == []


async def test_volver_al_default_de_quien_dejaria_sin_nadie_tambien_es_409():
    app, conn, ids = _escenario()
    # el empleado recibe por una fila propia; el owner y el aprobador la desactivan; volver el empleado al default
    # (donde no recibe) dejaría a todos sin avisos
    conn.poner_config(ids["owner"], recibe=False, email=False)
    conn.poner_config(ids["aprobador"], recibe=False, email=False)
    conn.poner_config(ids["empleado"], recibe=True, email=True)
    r = await _put(app, [_cambio(ids["empleado"], por_defecto=True)])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert ids["empleado"] in conn.config and conn.auditoria == []


# --- entradas inválidas: nunca 500 -------------------------------------------------------------------------------


async def test_whatsapp_se_ve_pero_no_se_puede_activar():
    app, conn, ids = _escenario()
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True, canal_whatsapp=True)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "canal_no_disponible"
    assert conn.config == {} and conn.auditoria == []
    # con por_defecto tampoco
    r = await _put(app, [_cambio(ids["empleado"], por_defecto=True, canal_whatsapp=True)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "canal_no_disponible"


@pytest.mark.parametrize(
    "cambio",
    [
        {"por_defecto": True, "recibe_avisos": True},
        {"por_defecto": True, "canal_email": False},
        {"recibe_avisos": True},
        {"canal_email": True},
        {},
    ],
)
async def test_un_cambio_es_por_defecto_o_las_dos_preferencias(cambio):
    app, conn, ids = _escenario()
    r = await _put(app, [_cambio(ids["empleado"], **cambio)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "cambio_invalido"
    assert conn.config == {} and conn.auditoria == []


async def test_la_misma_persona_dos_veces_es_422():
    app, conn, ids = _escenario()
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
            _cambio(ids["empleado"], recibe_avisos=False, canal_email=False),
        ],
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_repetido"
    assert conn.config == {} and conn.auditoria == []


async def test_una_persona_de_otro_negocio_o_inexistente_es_422_y_no_se_escribe_nada():
    app, conn, ids = _escenario()
    for ajeno in (ids["ajeno"], uuid.uuid4()):
        r = await _put(
            app,
            [
                _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
                _cambio(ajeno, recibe_avisos=True, canal_email=True),
            ],
        )
        assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_inexistente"
        # el mensaje es el mismo para las dos: no revela si la persona existe en otro negocio
        assert str(ajeno) not in r.text
    assert conn.config == {} and conn.auditoria == []


async def test_una_persona_desactivada_es_409_y_no_se_escribe_nada():
    app, conn, ids = _escenario()
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
            _cambio(ids["inactivo"], recibe_avisos=True, canal_email=True),
        ],
    )
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "staff_inactivo"
    assert conn.config == {} and conn.auditoria == []


async def test_el_tope_de_cambios_y_la_forma_del_cuerpo_se_validan_antes_de_tocar_la_base():
    app, conn, ids = _escenario()
    cien = [_cambio(uuid.uuid4(), por_defecto=True) for _ in range(100)]
    for cuerpo in (
        {"cambios": cien + [_cambio(uuid.uuid4(), por_defecto=True)]},  # 101
        {"cambios": []},
        {},
        {"cambios": [{"staff_id": "no-es-un-uuid", "por_defecto": True}]},
        {"cambios": [_cambio(ids["empleado"], recibe_avisos="si", canal_email=True)]},
        {"cambios": [_cambio(ids["empleado"], recibe_avisos=1, canal_email=True)]},
        {"cambios": "texto"},
    ):
        r = await http(app).put("/local/avisos", json=cuerpo, headers=_h())
        assert r.status_code == 422, cuerpo
    assert [s for s in conn.sentencias() if "avisos_negocio_config" in s] == []
    # justo en el tope se acepta la forma (las personas no existen: 422 de dominio, no de forma)
    r = await http(app).put("/local/avisos", json={"cambios": cien}, headers=_h())
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_inexistente"


async def test_cien_cambios_validos_se_aplican_en_un_solo_pedido():
    app, conn, ids = _escenario()
    nuevos = [conn.agregar(TENANT, permisos=("view_bookings",)) for _ in range(100)]
    r = await _put(app, [_cambio(i, recibe_avisos=True, canal_email=True) for i in nuevos])
    assert r.status_code == 200, r.text
    assert len(conn.config) == 100 and len(conn.auditoria) == 1


# --- fallos de la base y concurrencia ---------------------------------------------------------------------------


async def test_un_deadlock_o_choque_de_concurrencia_es_409_reintentable_y_no_deja_nada():
    app, conn, ids = _escenario()
    conn.falla_al_escribir = DeadlockDetectedError("deadlock detected")
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)])
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "conflicto_de_concurrencia" and r.json()["detail"]["reintentable"] is True
    assert conn.config == {} and conn.auditoria == []


async def test_un_check_o_una_clave_foranea_inesperados_de_la_base_son_4xx_nunca_500():
    app, conn, ids = _escenario()
    conn.falla_al_escribir = CheckViolationError("un check que no conocemos")
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "valor_invalido"
    # la persona desapareció entre el bloqueo y la escritura
    conn.falla_al_escribir = ForeignKeyViolationError("violates foreign key constraint")
    r = await _put(app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_inexistente"
    assert conn.config == {} and conn.auditoria == []


async def test_las_personas_se_bloquean_en_orden_de_id_antes_de_escribir():
    app, conn, ids = _escenario()
    r = await _put(
        app,
        [
            _cambio(ids["empleado"], recibe_avisos=True, canal_email=True),
            _cambio(ids["aprobador"], recibe_avisos=True, canal_email=True),
        ],
    )
    assert r.status_code == 200, r.text
    sentencias = conn.sentencias()
    assert sentencias.index(a.SQL_BLOQUEAR_STAFF) < sentencias.index(a.SQL_INSERTAR)
    assert "for update" in a.SQL_BLOQUEAR_STAFF and "order by id" in a.SQL_BLOQUEAR_STAFF
    bloqueo = [args for sql, args in conn.ejecutado if sql == a.SQL_BLOQUEAR_STAFF][0]
    assert list(bloqueo[1]) == sorted(bloqueo[1], key=str)
    # la garantía de destinatarios se difiere mientras se escribe y se exige al final, antes de auditar
    assert sentencias.index(a.SQL_DIFERIR_GARANTIA) < sentencias.index(a.SQL_INSERTAR)
    assert sentencias.index(a.SQL_INSERTAR) < sentencias.index(a.SQL_EXIGIR_GARANTIA)


# --- aislamiento entre tenants ----------------------------------------------------------------------------------


async def test_un_negocio_no_ve_ni_modifica_la_configuracion_de_otro():
    app, conn, ids = _escenario()
    otro_empleado = conn.agregar(OTRO_TENANT, permisos=("view_bookings",))
    conn.poner_config(ids["ajeno"], recibe=True, email=True)
    antes = json.dumps({str(k): v for k, v in conn.config.items()}, sort_keys=True)
    # quien administra el otro negocio ve solo a los suyos
    por_id = _por_id(await http(app).get("/local/avisos", headers=_h(owner=True, escribe=False, tenant=OTRO_TENANT)))
    assert set(por_id) == {str(ids["ajeno"]), str(otro_empleado)}
    # y no puede tocar a las personas del primero
    r = await _put(
        app, [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)], owner=True, tenant=OTRO_TENANT
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_inexistente"
    r = await _put(app, [_cambio(ids["owner"], por_defecto=True)], owner=True, tenant=OTRO_TENANT)
    assert r.status_code == 422
    assert json.dumps({str(k): v for k, v in conn.config.items()}, sort_keys=True) == antes
    assert conn.auditoria == []


# --- forma del router ---------------------------------------------------------------------------------------------


def test_el_router_es_de_modulo_con_las_dos_rutas():
    assert isinstance(rutas_avisos.router, APIRouter)
    assert {(m, r.path) for r in rutas_avisos.router.routes for m in r.methods} == {
        ("GET", "/local/avisos"),
        ("PUT", "/local/avisos"),
    }


@pytest.mark.parametrize("metodo", ["GET", "PUT"])
async def test_cada_endpoint_usa_una_sola_transaccion(metodo):
    app, conn, ids = _escenario()
    cuerpo = {"cambios": [_cambio(ids["empleado"], recibe_avisos=True, canal_email=True)]} if metodo == "PUT" else None
    antes = conn.sentencias().count("BEGIN")
    r = await http(app).request(metodo, "/local/avisos", json=cuerpo, headers=_h(escribe=metodo == "PUT"))
    assert r.status_code == 200, r.text
    assert conn.sentencias().count("BEGIN") - antes == 1
