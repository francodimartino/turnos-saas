"""Endpoints /clientes sin base real: permisos, validación, dedup, orden commit -> limpieza de calendario."""

import json
import logging
import uuid

import pytest

from dashboard_backend.clientes import CAMPOS_EXTRA_SETTING, ErrorExtraData, validar_extra_data
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_clientes import (
    CalendarioSimulado,
    app_clientes,
    evento_no_limpiado,
)

TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
TEL = "+54 9 11 5550-0101"
TEL_E164 = "+5491155500101"
VARIABLES_GOOGLE = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)


def _h(escribe=False, **kw):
    h = {"Cookie": "dash_acceso=" + token(user_id=STAFF, tenant_id=TENANT, **kw)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _inserts(conn):
    return [a for s, a in conn.ejecutado if s.lstrip().startswith("insert into public.end_customers")]


# --- extra_data configurable por tenant (lógica pura) -------------------------------------------------------


def test_extra_data_vacio_siempre_es_valido():
    assert validar_extra_data({}, None) == {}
    assert validar_extra_data({}, []) == {}


def test_sin_campos_configurados_no_se_acepta_extra_data():
    with pytest.raises(ErrorExtraData):
        validar_extra_data({"alergias": "ninguna"}, None)


def test_solo_se_aceptan_los_campos_que_el_tenant_configuro():
    campos = ["alergias", {"key": "cumple", "label": "Cumpleaños"}]
    assert validar_extra_data({"alergias": "latex", "cumple": "03-05"}, campos) == {
        "alergias": "latex",
        "cumple": "03-05",
    }
    with pytest.raises(ErrorExtraData) as e:
        validar_extra_data({"otro": "x"}, campos)
    assert "otro" in str(e.value)


@pytest.mark.parametrize("valor", [{"a": 1}, [1], "x" * 501])
def test_los_valores_de_extra_data_son_escalares_acotados(valor):
    with pytest.raises(ErrorExtraData):
        validar_extra_data({"alergias": valor}, ["alergias"])


def test_la_configuracion_mal_formada_se_trata_como_sin_campos():
    with pytest.raises(ErrorExtraData):
        validar_extra_data({"x": "y"}, "no es una lista")


# --- permisos por endpoint ----------------------------------------------------------------------------------

ENDPOINTS = [
    ("get", "/clientes", None, "view_bookings"),
    ("get", f"/clientes/{uuid.uuid4()}", None, "view_bookings"),
    ("post", "/clientes", {"phone": TEL, "name": "Cliente de prueba"}, "approve_bookings"),
    ("patch", f"/clientes/{uuid.uuid4()}", {"name": "Otro nombre"}, "approve_bookings"),
    ("post", f"/clientes/{uuid.uuid4()}/anonimizar", None, "manage_staff"),
]


async def _llamar(app, metodo, ruta, cuerpo, headers):
    kw = {"json": cuerpo} if cuerpo else {}
    return await getattr(http(app), metodo)(ruta, headers=headers, **kw)


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_sin_el_permiso_el_endpoint_responde_403_y_no_toca_clientes(metodo, ruta, cuerpo, permiso):
    app, conn, _ = app_clientes()
    conn.estado_staff["tiene"] = False
    r = await _llamar(app, metodo, ruta, cuerpo, _h(escribe=metodo != "get"))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert not conn.sentencias_que_contienen("end_customers")


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_el_permiso_que_se_consulta_es_el_documentado(metodo, ruta, cuerpo, permiso):
    app, conn, _ = app_clientes()
    await _llamar(app, metodo, ruta, cuerpo, _h(escribe=metodo != "get"))
    consultas = [args for sql, args in conn.ejecutado if "tenant_user_permissions" in sql]
    assert consultas and consultas[0][2] == permiso


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "permiso"), ENDPOINTS)
async def test_sin_sesion_es_401(metodo, ruta, cuerpo, permiso):
    app, _, _ = app_clientes()
    r = await _llamar(app, metodo, ruta, cuerpo, {"Origin": ORIGEN})
    assert r.status_code == 401


async def test_no_existe_ninguna_ruta_delete_sobre_clientes():
    app, conn, _ = app_clientes()
    r = await http(app).delete(f"/clientes/{uuid.uuid4()}", headers=_h(escribe=True))
    assert r.status_code == 405
    assert not conn.sentencias_que_contienen("end_customers")


# --- alta manual ---------------------------------------------------------------------------------------------


async def test_alta_manual_guarda_el_telefono_en_e164_y_el_tenant_de_la_sesion():
    app, conn, _ = app_clientes()
    r = await http(app).post(
        "/clientes",
        json={"phone": TEL, "name": "  Cliente de prueba ", "tenant_id": str(uuid.uuid4())},
        headers=_h(escribe=True),
    )
    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["phone"] == TEL_E164
    assert cuerpo["name"] == "Cliente de prueba"
    assert cuerpo["sin_cliente_identificable"] is False
    insertar = _inserts(conn)
    assert insertar[0][0] == TENANT  # el del JWT; el del body se ignora
    assert insertar[0][1] == TEL_E164


@pytest.mark.parametrize("phone", ["no es un telefono", "+54 11", "5550", ""])
async def test_alta_con_telefono_invalido_es_422_sin_repetir_el_valor(phone):
    app, conn, _ = app_clientes()
    r = await http(app).post("/clientes", json={"phone": phone, "name": "Cliente"}, headers=_h(escribe=True))
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "telefono_invalido"
    assert "5550" not in r.text
    assert not _inserts(conn)


async def test_alta_con_telefono_ya_cargado_es_409_con_el_id_existente():
    app, conn, _ = app_clientes()
    conn.duplicado = uuid.uuid4()
    r = await http(app).post("/clientes", json={"phone": TEL, "name": "Cliente"}, headers=_h(escribe=True))
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "codigo": "cliente_duplicado",
        "mensaje": "Ya existe un cliente con ese teléfono",
        "cliente_id": str(conn.duplicado),
    }
    assert not _inserts(conn)


async def test_el_alta_serializa_por_telefono_antes_de_buscar_duplicados():
    app, conn, _ = app_clientes()
    await http(app).post("/clientes", json={"phone": TEL, "name": "Cliente"}, headers=_h(escribe=True))
    sentencias = conn.sentencias()
    bloqueo = next(i for i, s in enumerate(sentencias) if "pg_advisory_xact_lock" in s)
    duplicado = next(i for i, s in enumerate(sentencias) if "deleted_at is null" in s and "phone = $2" in s)
    assert bloqueo < duplicado


async def test_alta_con_extra_data_fuera_de_la_configuracion_del_tenant_es_422():
    app, conn, _ = app_clientes()
    conn.settings = {CAMPOS_EXTRA_SETTING: ["alergias"]}
    ok = await http(app).post(
        "/clientes",
        json={"phone": TEL, "name": "Cliente", "extra_data": {"alergias": "latex"}},
        headers=_h(escribe=True),
    )
    assert ok.status_code == 201
    assert json.loads(_inserts(conn)[0][3]) == {"alergias": "latex"}
    mal = await http(app).post(
        "/clientes",
        json={"phone": "+54 9 11 5550-0102", "name": "Cliente", "extra_data": {"peso": 70}},
        headers=_h(escribe=True),
    )
    assert mal.status_code == 422
    assert mal.json()["detail"]["codigo"] == "extra_data_invalido"


# --- edición -------------------------------------------------------------------------------------------------


async def test_editar_un_cliente_anonimizado_es_409_y_uno_inexistente_404():
    app, conn, _ = app_clientes()
    anon = conn._fila(tenant_id=TENANT, deleted_at="2026-01-02T00:00:00+00:00")
    conn.clientes[anon["id"]] = anon
    r = await http(app).patch(f"/clientes/{anon['id']}", json={"name": "Nuevo"}, headers=_h(escribe=True))
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "cliente_anonimizado"
    r = await http(app).patch(f"/clientes/{uuid.uuid4()}", json={"name": "Nuevo"}, headers=_h(escribe=True))
    assert r.status_code == 404


async def test_editar_solo_toca_los_campos_enviados_y_normaliza_el_telefono():
    app, conn, _ = app_clientes()
    fila = conn._fila(tenant_id=TENANT, phone=TEL_E164, name="Cliente")
    conn.clientes[fila["id"]] = fila
    r = await http(app).patch(f"/clientes/{fila['id']}", json={"phone": "+54 9 11 5550 0199"}, headers=_h(escribe=True))
    assert r.status_code == 200
    assert r.json()["phone"] == "+5491155500199"
    assert r.json()["name"] == "Cliente"


@pytest.mark.parametrize("phone", ["011 15-5550-0101", "11 5550-0101", "5491155500101", "+54 11 5550-0101"])
async def test_alta_acepta_numeros_argentinos_sin_mas_y_los_guarda_como_celular_e164(phone):
    app, conn, _ = app_clientes()
    r = await http(app).post("/clientes", json={"phone": phone, "name": "Cliente"}, headers=_h(escribe=True))
    assert r.status_code == 201
    assert r.json()["phone"] == TEL_E164
    assert _inserts(conn)[0][1] == TEL_E164


# --- anonimización: commit primero, calendario después --------------------------------------------------------


def _cliente_con_eventos(conn, n=2):
    fila = conn._fila(tenant_id=TENANT, phone=TEL_E164, name="Cliente")
    conn.clientes[fila["id"]] = fila
    conn.targets = [{"integration_id": uuid.uuid4(), "google_calendar_event_id": f"evt-{i}"} for i in range(n)]
    return fila["id"]


async def test_anonimizar_hace_commit_y_recien_despues_limpia_el_calendario():
    app, conn, cal = app_clientes(servicio=CalendarioSimulado)
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 200
    antes = cal.sentencias_al_limpiar
    i_anon = next(i for i, s in enumerate(antes) if "anonymize_end_customer" in s)
    i_commit = next(i for i, s in enumerate(antes) if s == "COMMIT" and i > i_anon)
    i_targets = next(i for i, s in enumerate(antes) if "calendar_scrub_targets" in s)
    assert i_anon < i_commit < i_targets
    assert [p[1] for p in cal.pares] == ["evt-0", "evt-1"]
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 2, "no_limpiados": 0}
    assert r.json()["sin_cliente_identificable"] is True


async def test_los_eventos_no_limpiados_quedan_como_calendar_scrub_failed_sin_pii():
    app, conn, _ = app_clientes(
        servicio=lambda c: CalendarioSimulado(c, fallidos=[evento_no_limpiado("evt-1", "token_vencido")])
    )
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 200
    assert r.json()["calendario"]["no_limpiados"] == 1
    assert len(conn.auditoria) == 1
    accion, entidad, entidad_id, metadata, _tenant = conn.auditoria[0]
    assert accion == "calendar_scrub_failed"
    assert entidad == "end_customers"
    assert entidad_id == cid
    assert json.loads(metadata) == {"event_id": "evt-1", "motivo": "token_vencido"}


async def test_un_evento_sin_integracion_tambien_se_registra_como_fallido():
    app, conn, cal = app_clientes(servicio=CalendarioSimulado)
    cid = _cliente_con_eventos(conn, n=1)
    conn.targets[0]["integration_id"] = None
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 200
    assert cal.pares is None  # sin pares no se llama al calendario
    assert json.loads(conn.auditoria[0][3]) == {"event_id": "evt-0", "motivo": "sin_integracion"}


async def test_sin_google_configurado_el_borrado_sigue_y_se_registra_el_motivo_de_configuracion(monkeypatch):
    for v in VARIABLES_GOOGLE:
        monkeypatch.delenv(v, raising=False)
    app, conn, _ = app_clientes()  # fábrica por defecto: lee el entorno
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 200
    assert conn.clientes[cid]["deleted_at"] is not None
    assert [json.loads(a[3]) for a in conn.auditoria] == [
        {"event_id": "evt-0", "motivo": "configuracion_calendar"},
        {"event_id": "evt-1", "motivo": "configuracion_calendar"},
    ]
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 2, "no_limpiados": 2}


async def test_si_el_calendario_falla_de_forma_inesperada_el_borrado_confirmado_responde_200():
    class Roto:
        def __init__(self, conn):
            pass

        async def limpiar_eventos(self, pares):
            raise RuntimeError("falla inesperada")

    app, conn, _ = app_clientes(servicio=Roto)
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 200
    assert conn.clientes[cid]["deleted_at"] is not None
    assert {json.loads(a[3])["motivo"] for a in conn.auditoria} == {"error_inesperado"}
    assert len(conn.auditoria) == 2


async def test_anonimizar_un_cliente_inexistente_o_ajeno_es_404_y_no_llama_a_la_funcion():
    app, conn, _ = app_clientes()
    r = await http(app).post(f"/clientes/{uuid.uuid4()}/anonimizar", headers=_h(escribe=True))
    assert r.status_code == 404
    assert not conn.sentencias_que_contienen("anonymize_end_customer")


async def test_anonimizar_emite_el_log_estructurado_obligatorio_sin_pii(caplog):
    app, conn, _ = app_clientes(servicio=CalendarioSimulado)
    cid = _cliente_con_eventos(conn)
    with caplog.at_level(logging.INFO, logger="observabilidad.eventos"):
        await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h(escribe=True))
    eventos = [r for r in caplog.records if getattr(r, "evento", None) == "anonymize_end_customer"]
    assert len(eventos) == 1
    assert eventos[0].end_customer_id == str(cid)
    assert "5550" not in json.dumps({k: str(v) for k, v in vars(eventos[0]).items()})
