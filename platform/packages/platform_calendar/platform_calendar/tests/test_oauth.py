import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from platform_calendar import (
    ConfigGoogle,
    ErrorConfiguracionCalendar,
    ErrorEstadoOAuth,
    ErrorOAuth,
    crear_calendario,
)

PROF = uuid.uuid4()
USUARIO = uuid.uuid4()
TENANT = uuid.uuid4()


def _servicio(config, almacen, google, **kw):
    return crear_calendario(config, almacen, google.cliente(), **kw)


def _state(url):
    return unquote(url.split("state=")[1].split("&")[0])


def test_config_desde_entorno(monkeypatch):
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"):
        monkeypatch.setenv(k, "v-" + k)
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "clave-de-estado-0123456789")
    c = ConfigGoogle.desde_entorno()
    assert c.client_id == "v-GOOGLE_OAUTH_CLIENT_ID"
    assert "v-GOOGLE_OAUTH_CLIENT_SECRET" not in repr(c)
    assert "clave-de-estado-0123456789" not in repr(c)


def test_config_desde_entorno_falta_variable_nombra_la_variable(monkeypatch):
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"):
        monkeypatch.setenv(k, "valor-que-no-debe-aparecer")
    monkeypatch.delenv("CALENDAR_OAUTH_STATE_SECRET", raising=False)
    with pytest.raises(ErrorConfiguracionCalendar) as e:
        ConfigGoogle.desde_entorno()
    assert "CALENDAR_OAUTH_STATE_SECRET" in str(e.value)
    assert "valor-que-no-debe-aparecer" not in str(e.value)


def test_url_de_autorizacion_trae_state_verificable(config, almacen, google):
    s = _servicio(config, almacen, google)
    url = s.url_autorizacion(TENANT, PROF, USUARIO)
    q = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "accounts.google.com"
    assert q["client_id"] == [config.client_id]
    assert q["redirect_uri"] == [config.redirect_uri]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["response_type"] == ["code"]
    assert "calendar.events" in q["scope"][0]
    assert config.client_secret not in url
    estado = s.verificar_state(q["state"][0])
    assert estado.tenant_id == TENANT and estado.professional_id == PROF and estado.user_id == USUARIO
    assert estado.nonce and estado.expira > 0


def test_state_alterado_o_de_otra_clave_se_rechaza(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    cuerpo, firma = state.split(".")
    for malo in (cuerpo + "." + firma[:-2] + "AA", "x" + cuerpo + "." + firma, "basura", "", cuerpo):
        with pytest.raises(ErrorEstadoOAuth):
            s.verificar_state(malo)
    clave = b"otra-clave-distinta-0123456789ab"
    otro = ConfigGoogle(config.client_id, config.client_secret, config.redirect_uri, clave)
    with pytest.raises(ErrorEstadoOAuth):
        _servicio(otro, almacen, google).verificar_state(state)


def test_state_vencido_se_rechaza(config, almacen, google):
    ahora = [1_000_000.0]
    s = _servicio(config, almacen, google, reloj=lambda: ahora[0])
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    s.verificar_state(state)
    ahora[0] += 601
    with pytest.raises(ErrorEstadoOAuth):
        s.verificar_state(state)


async def test_completar_autorizacion_guarda_credenciales_por_profesional(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    integ = await s.completar_autorizacion("codigo-ok", state, TENANT, USUARIO)
    assert almacen.por_profesional[PROF] == integ
    _, cfg, secreto = almacen.integraciones[integ]
    assert google.refresh_emitido in secreto.revelar()
    assert google.refresh_emitido not in repr(cfg) and google.access_emitido not in repr(cfg)
    metodo, _, _, cuerpo = google.pedidos[0]
    assert metodo == "POST" and "grant_type=authorization_code" in cuerpo and "code=codigo-ok" in cuerpo


async def test_completar_autorizacion_rechaza_tenant_distinto(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    with pytest.raises(ErrorEstadoOAuth):
        await s.completar_autorizacion("codigo-ok", state, uuid.uuid4(), USUARIO)
    assert google.pedidos == [] and almacen.integraciones == {}


async def test_completar_autorizacion_rechaza_usuario_distinto_sin_llamar_a_google(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    with pytest.raises(ErrorEstadoOAuth):
        await s.completar_autorizacion("codigo-ok", state, TENANT, uuid.uuid4())
    assert google.pedidos == [] and almacen.integraciones == {}


def test_cada_state_lleva_un_nonce_distinto(config, almacen, google):
    s = _servicio(config, almacen, google)
    a = s.verificar_state(_state(s.url_autorizacion(TENANT, PROF, USUARIO)))
    b = s.verificar_state(_state(s.url_autorizacion(TENANT, PROF, USUARIO)))
    assert a.nonce != b.nonce


def test_state_sin_usuario_o_sin_nonce_se_rechaza(config, almacen, google):
    import base64
    import json

    s = _servicio(config, almacen, google)
    for quitar in ("u", "n"):
        cuerpo_ok = _state(s.url_autorizacion(TENANT, PROF, USUARIO)).split(".")[0]
        carga = json.loads(base64.urlsafe_b64decode(cuerpo_ok + "=" * (-len(cuerpo_ok) % 4)))
        del carga[quitar]
        cuerpo = base64.urlsafe_b64encode(json.dumps(carga).encode()).rstrip(b"=").decode()
        with pytest.raises(ErrorEstadoOAuth):
            s.verificar_state(cuerpo + "." + s._firmar(cuerpo))


async def test_codigo_invalido_o_sin_refresh_token_falla_sin_filtrar_secretos(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    for codigo in ("codigo-malo", "sin-refresh"):
        with pytest.raises(ErrorOAuth) as e:
            await s.completar_autorizacion(codigo, state, TENANT, USUARIO)
        for valor in (config.client_secret, google.access_emitido, google.refresh_emitido):
            assert valor not in str(e.value) and valor not in repr(e.value)
    assert almacen.integraciones == {}


async def test_refresco_de_token_usa_refresh_token_guardado_y_cachea(config, almacen, google):
    s = _servicio(config, almacen, google)
    state = _state(s.url_autorizacion(TENANT, PROF, USUARIO))
    integ = await s.completar_autorizacion("codigo-ok", state, TENANT, USUARIO)
    google.pedidos.clear()
    t1 = await s.token_de_acceso(integ)
    t2 = await s.token_de_acceso(integ)
    assert t1.revelar() == google.access_emitido == t2.revelar()
    assert len(google.pedidos) == 1  # el segundo salió del caché en memoria
    assert "grant_type=refresh_token" in google.pedidos[0][3]
    assert google.access_emitido not in repr(t1)
