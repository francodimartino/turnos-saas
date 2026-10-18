"""Conexión de Google Calendar por profesional con conexión falsa y Google simulado (ARCHITECTURE.md §7.1, §2.11.5).

Quién puede conectar, validación del `state`, mapeo de errores y que nunca se escriba directo a `tenant_integrations`.
"""

import json
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_calendar import GoogleSimulado, app_calendar

TENANT = uuid.uuid4()
OTRO_TENANT = uuid.uuid4()
YO = uuid.uuid4()
VARIABLES_GOOGLE = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)


def test_el_dashboard_sigue_exigiendo_calendar_oauth_state_secret(monkeypatch):
    from platform_calendar import ErrorConfiguracionCalendar

    from dashboard_backend.calendario import config_google_desde_entorno

    for variable in VARIABLES_GOOGLE:
        monkeypatch.setenv(variable, "valor-de-prueba-0123456789")
    monkeypatch.delenv("CALENDAR_OAUTH_STATE_SECRET")
    with pytest.raises(ErrorConfiguracionCalendar, match="CALENDAR_OAUTH_STATE_SECRET"):
        config_google_desde_entorno()


def _h(*, owner=False, escribe=True, tenant=TENANT):
    aal = "aal2" if owner else "aal1"
    h = {"Cookie": "dash_acceso=" + token(user_id=YO, tenant_id=tenant, is_owner=owner, aal=aal)}
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _como(conn, *, owner=False, manage_staff=False):
    conn.estado_staff = {"is_owner": owner, "is_active": True, "tiene": manage_staff}


def _resultado(respuesta) -> dict[str, list[str]]:
    """El resultado del POST del callback en la forma `{"google_calendar": [resultado], ...}`."""
    if respuesta.status_code == 200:
        return {"google_calendar": [respuesta.json()["resultado"]], "profesional": [respuesta.json()["profesional_id"]]}
    assert respuesta.status_code >= 400, respuesta.text
    return {"google_calendar": [respuesta.json()["detail"]["codigo"]]}


# --- iniciar la conexión: quién puede ----------------------------------------------------------------------------


async def test_el_profesional_vinculado_conecta_el_suyo_sin_permisos_extra():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h())
    assert r.status_code == 200, r.text
    url = urlsplit(r.json()["url"])
    assert url.netloc == "accounts.google.com"
    estado = google.servicio(None).verificar_state(parse_qs(url.query)["state"][0])
    assert (estado.tenant_id, estado.professional_id, estado.user_id) == (TENANT, prof, YO)
    assert parse_qs(url.query)["client_id"] == [google.config.client_id]


async def test_otro_profesional_exige_manage_staff_u_owner():
    app, conn, _ = app_calendar()
    ajeno = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
    sin_vinculo = conn.agregar_profesional(TENANT)
    _como(conn)
    for prof in (ajeno, sin_vinculo):
        r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h())
        assert r.status_code == 403, r.text
        assert "url" not in r.json()

    _como(conn, manage_staff=True)
    assert (await http(app).post(f"/profesionales/{ajeno}/calendar/conexion", headers=_h())).status_code == 200
    _como(conn, owner=True)
    r = await http(app).post(f"/profesionales/{sin_vinculo}/calendar/conexion", headers=_h(owner=True))
    assert r.status_code == 200


async def test_iniciar_profesional_inexistente_o_de_otro_tenant_es_404():
    app, conn, _ = app_calendar()
    de_otro = conn.agregar_profesional(OTRO_TENANT)
    _como(conn, owner=True)
    for prof in (uuid.uuid4(), de_otro):
        r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h(owner=True))
        assert r.status_code == 404, r.text


async def test_iniciar_con_profesional_inactivo_es_409():
    app, conn, _ = app_calendar()
    prof = conn.agregar_profesional(TENANT, activo=False, vinculado_a=YO)
    _como(conn)
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h())
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "profesional_inactivo"


async def test_iniciar_sin_sesion_o_sin_origen_se_rechaza():
    app, conn, _ = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers={"Origin": ORIGEN})
    assert r.status_code == 401
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h(escribe=False))
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "origen_no_permitido"


async def test_sin_google_configurado_iniciar_y_callback_responden_503_claro(monkeypatch):
    for variable in VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    app, conn, _ = app_calendar(usar_fabrica_por_defecto=True)
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h())
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "calendar_no_configurado"
    r = await http(app).post("/calendar/callback", json={"code": "c", "state": "s"}, headers=_h())
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "calendar_no_configurado"
    for variable in VARIABLES_GOOGLE:
        assert variable not in r.text


# --- callback (POST desde la página del dashboard; el GET no tiene efecto) ---------------------------------------


async def test_callback_get_no_escribe_ni_llama_a_google_y_no_refleja_code_ni_state():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    state = google.state_de(TENANT, prof, YO)
    r = await http(app).get(
        "/calendar/callback", params={"code": "codigo-de-google", "state": state}, headers=_h(escribe=False)
    )
    assert r.status_code == 303
    destino = urlsplit(r.headers["location"])
    assert f"{destino.scheme}://{destino.netloc}" == ORIGEN
    assert "codigo-de-google" not in r.headers["location"] and state not in r.headers["location"]
    assert google.pedidos == [] and conn.integraciones == {} and conn.auditoria == []
    assert conn.sentencias_de_escritura_directa() == [] and conn.nonces_consumidos == set()
    # Ni siquiera hace falta sesión: no hay nada que proteger porque no hace nada.
    r = await http(app).get("/calendar/callback", params={"code": "c", "state": state})
    assert r.status_code == 303 and google.pedidos == [] and conn.nonces_consumidos == set()


async def test_state_de_otro_usuario_del_mismo_tenant_se_rechaza_sin_llamar_a_google():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn, owner=True)
    otro_usuario = uuid.uuid4()
    # El state lo generó el usuario A (`otro_usuario`); lo completa la sesión B (`YO`), del mismo tenant.
    r = await http(app).post(
        "/calendar/callback",
        json={"code": "c", "state": google.state_de(TENANT, prof, otro_usuario)},
        headers=_h(owner=True),
    )
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "state_invalido"
    assert google.pedidos == [] and conn.integraciones == {} and conn.nonces_consumidos == set()


async def test_reusar_el_mismo_state_devuelve_state_invalido():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    cuerpo = {"code": "c", "state": google.state_de(TENANT, prof, YO)}
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "state_invalido"
    assert len(google.pedidos) == 1  # el segundo intento no llegó a Google


async def test_quien_no_puede_conectar_a_ese_profesional_no_gasta_el_nonce():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
    _como(conn)
    r = await http(app).post(
        "/calendar/callback", json={"code": "c", "state": google.state_de(TENANT, prof, YO)}, headers=_h()
    )
    assert r.status_code == 403 and conn.nonces_consumidos == set()


async def test_callback_guarda_solo_el_refresh_token_y_redirige_con_ok():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    state = google.state_de(TENANT, prof, YO)
    params = {"code": "codigo-de-google", "state": state}
    r = await http(app).post("/calendar/callback", json=params, headers=_h())
    consulta = _resultado(r)
    assert consulta["google_calendar"] == ["ok"] and consulta["profesional"] == [str(prof)]
    # Ni el código ni el state ni tokens vuelven en la respuesta.
    assert "codigo-de-google" not in r.text and state not in r.text and google.refresh_token not in r.text

    assert len(google.pedidos) == 1
    assert "codigo-de-google" in google.pedidos[0].content.decode()
    guardado = conn.integraciones[prof]
    assert json.loads(guardado["secret"]) == {"refresh_token": google.refresh_token}
    assert conn.auditoria == [("connect_google_calendar", prof)]
    # Solo la función SECURITY DEFINER escribe: nunca un INSERT/UPDATE/DELETE directo.
    assert conn.sentencias_de_escritura_directa() == []


@pytest.mark.parametrize("como", ["propio_sin_permisos", "manage_staff", "owner"])
async def test_callback_respeta_el_mismo_permiso_que_iniciar(como):
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO if como == "propio_sin_permisos" else uuid.uuid4())
    _como(conn, owner=como == "owner", manage_staff=como == "manage_staff")
    r = await http(app).post(
        "/calendar/callback", json={"code": "c", "state": google.state_de(TENANT, prof, YO)}, headers=_h()
    )
    assert _resultado(r)["google_calendar"] == ["ok"]


async def test_callback_sin_permiso_sobre_ese_profesional_no_llama_a_google_ni_guarda():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
    _como(conn)
    r = await http(app).post(
        "/calendar/callback", json={"code": "c", "state": google.state_de(TENANT, prof, YO)}, headers=_h()
    )
    assert _resultado(r)["google_calendar"] == ["sin_permiso"]
    assert google.pedidos == [] and conn.integraciones == {}


async def test_callback_con_state_de_otro_tenant_se_rechaza():
    app, conn, google = app_calendar()
    de_otro = conn.agregar_profesional(OTRO_TENANT, vinculado_a=YO)
    _como(conn, owner=True)
    r = await http(app).post(
        "/calendar/callback",
        json={"code": "c", "state": google.state_de(OTRO_TENANT, de_otro, YO)},
        headers=_h(owner=True),
    )
    assert _resultado(r)["google_calendar"] == ["state_invalido"]
    assert google.pedidos == [] and conn.integraciones == {}


@pytest.mark.parametrize("tipo", ["vencido", "firma_ajena", "alterado", "basura", "vacio"])
async def test_callback_con_state_invalido_no_llama_a_google(tipo):
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    valido = google.state_de(TENANT, prof, YO)
    state = {
        "vencido": google.state_de(TENANT, prof, YO, antes_seg=3600),
        "firma_ajena": GoogleSimulado().state_de(TENANT, prof, YO),
        "alterado": valido[:-3] + ("AAA" if not valido.endswith("AAA") else "BBB"),
        "basura": "esto-no-es-un-state",
        "vacio": "",
    }[tipo]
    r = await http(app).post("/calendar/callback", json={"code": "c", "state": state}, headers=_h())
    assert _resultado(r)["google_calendar"] == ["state_invalido"]
    assert google.pedidos == [] and conn.integraciones == {}


async def test_callback_con_profesional_inactivo_o_inexistente():
    app, conn, google = app_calendar()
    inactivo = conn.agregar_profesional(TENANT, activo=False, vinculado_a=YO)
    _como(conn, owner=True)
    for prof in (inactivo, uuid.uuid4()):
        r = await http(app).post(
            "/calendar/callback",
            json={"code": "c", "state": google.state_de(TENANT, prof, YO)},
            headers=_h(owner=True),
        )
        assert _resultado(r)["google_calendar"] == ["profesional_invalido"]
    assert google.pedidos == [] and conn.integraciones == {}


async def test_callback_cuando_el_usuario_cancela_en_google():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).post(
        "/calendar/callback",
        json={"error": "access_denied", "state": google.state_de(TENANT, prof, YO)},
        headers=_h(),
    )
    assert _resultado(r)["google_calendar"] == ["cancelado"]
    assert google.pedidos == [] and conn.integraciones == {}


async def test_callback_sin_code_se_trata_como_cancelado():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).post("/calendar/callback", json={"state": google.state_de(TENANT, prof, YO)}, headers=_h())
    assert _resultado(r)["google_calendar"] == ["cancelado"]


@pytest.mark.parametrize(
    ("google", "esperado"),
    [
        (GoogleSimulado(estado=400, cuerpo={"error": "invalid_grant"}), "google_rechazo"),
        (GoogleSimulado(cuerpo={"access_token": "solo-acceso"}), "google_rechazo"),
        (GoogleSimulado(red=True), "google_no_disponible"),
    ],
)
async def test_callback_ante_fallos_de_google_no_guarda_nada(google, esperado):
    app, conn, google = app_calendar(google=google)
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).post(
        "/calendar/callback", json={"code": "c", "state": google.state_de(TENANT, prof, YO)}, headers=_h()
    )
    assert _resultado(r)["google_calendar"] == [esperado]
    assert conn.integraciones == {}


async def test_callback_sin_sesion_es_401():
    app, _, google = app_calendar()
    state = google.state_de(TENANT, uuid.uuid4(), YO)
    r = await http(app).post("/calendar/callback", json={"code": "c", "state": state}, headers={"Origin": ORIGEN})
    assert r.status_code == 401


# --- callback (POST, para la página del dashboard que recibe el redirect de Google) ------------------------------


async def test_callback_post_devuelve_json_y_exige_origen():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    cuerpo = {"code": "codigo-de-google", "state": google.state_de(TENANT, prof, YO)}
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h(escribe=False))
    assert r.status_code == 403 and conn.integraciones == {}

    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json() == {"resultado": "ok", "profesional_id": str(prof)}
    assert prof in conn.integraciones


@pytest.mark.parametrize(
    ("caso", "estado_http", "resultado"),
    [("otro_tenant", 400, "state_invalido"), ("sin_permiso", 403, "sin_permiso")],
)
async def test_callback_post_mapea_el_resultado_a_un_estado_http(caso, estado_http, resultado):
    app, conn, google = app_calendar()
    if caso == "otro_tenant":
        prof = conn.agregar_profesional(OTRO_TENANT)
        state = google.state_de(OTRO_TENANT, prof, YO)
    else:
        prof = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
        state = google.state_de(TENANT, prof, YO)
    _como(conn)
    r = await http(app).post("/calendar/callback", json={"code": "c", "state": state}, headers=_h())
    assert r.status_code == estado_http, r.text
    assert r.json()["detail"]["codigo"] == resultado
    assert google.pedidos == [] and conn.integraciones == {}


# --- estado y desconexión ----------------------------------------------------------------------------------------


async def test_estado_de_conexion_sin_exponer_credenciales():
    app, conn, _ = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h(escribe=False))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "profesional_id": str(prof),
        "conectado": False,
        "conectado_desde": None,
        "google_configurado": True,
    }

    conn.conectar(prof)
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h(escribe=False))
    cuerpo = r.json()
    assert cuerpo["conectado"] is True and cuerpo["conectado_desde"].startswith("2030-01-01")
    assert set(cuerpo) == {"profesional_id", "conectado", "conectado_desde", "google_configurado"}


async def test_estado_funciona_sin_google_configurado_e_informa_que_no_lo_esta(monkeypatch):
    for variable in VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    app, conn, _ = app_calendar(usar_fabrica_por_defecto=True)
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    _como(conn)
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h(escribe=False))
    assert r.status_code == 200 and r.json()["google_configurado"] is False


async def test_estado_exige_ser_el_profesional_o_manage_staff_y_aisla_tenants():
    app, conn, _ = app_calendar()
    ajeno = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
    de_otro = conn.agregar_profesional(OTRO_TENANT)
    _como(conn)
    assert (await http(app).get(f"/profesionales/{ajeno}/calendar", headers=_h(escribe=False))).status_code == 403
    _como(conn, manage_staff=True)
    assert (await http(app).get(f"/profesionales/{ajeno}/calendar", headers=_h(escribe=False))).status_code == 200
    assert (await http(app).get(f"/profesionales/{de_otro}/calendar", headers=_h(escribe=False))).status_code == 404


async def test_desconectar_usa_la_funcion_sql_y_es_idempotente():
    app, conn, google = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    conn.conectar(prof)
    _como(conn)
    r = await http(app).delete(f"/profesionales/{prof}/calendar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json() == {"profesional_id": str(prof), "conectado": False, "habia_conexion": True}
    assert conn.integraciones == {} and conn.auditoria == [("disconnect_google_calendar", prof)]
    assert conn.sentencias_de_escritura_directa() == []

    r = await http(app).delete(f"/profesionales/{prof}/calendar", headers=_h())
    assert r.status_code == 200 and r.json()["habia_conexion"] is False
    assert google.pedidos == []


async def test_desconectar_sin_permiso_u_otro_tenant():
    app, conn, _ = app_calendar()
    ajeno = conn.agregar_profesional(TENANT, vinculado_a=uuid.uuid4())
    de_otro = conn.agregar_profesional(OTRO_TENANT)
    conn.conectar(ajeno)
    conn.conectar(de_otro)
    _como(conn)
    assert (await http(app).delete(f"/profesionales/{ajeno}/calendar", headers=_h())).status_code == 403
    assert ajeno in conn.integraciones
    _como(conn, owner=True)
    assert (await http(app).delete(f"/profesionales/{de_otro}/calendar", headers=_h(owner=True))).status_code == 404
    assert de_otro in conn.integraciones
    assert (await http(app).delete(f"/profesionales/{ajeno}/calendar", headers=_h(owner=True))).status_code == 200


async def test_desconectar_funciona_aunque_google_no_este_configurado(monkeypatch):
    for variable in VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    app, conn, _ = app_calendar(usar_fabrica_por_defecto=True)
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    conn.conectar(prof)
    _como(conn)
    r = await http(app).delete(f"/profesionales/{prof}/calendar", headers=_h())
    assert r.status_code == 200 and r.json()["habia_conexion"] is True
