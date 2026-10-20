"""Cliente HTTP de Supabase Auth (GoTrue REST con la anon key), probado con respuestas simuladas."""

import uuid

import httpx
import pytest
from turnos_supabase_proxy import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CodigoMFAInvalido,
    ContrasenaDebil,
    CredencialesInvalidas,
    InvitacionInvalida,
    LimiteExcedido,
    SesionExpirada,
    SolicitudRechazada,
)

from dashboard_backend.tests.soporte import ANON_KEY, SupabaseSimulado, sesion_json

FACTOR = str(uuid.uuid4())


async def test_login_usa_grant_password_con_la_anon_key():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json())
    tokens = await sb.cliente().login_password("a@b.co", "clave-larga-1")
    req = sb.llamadas[0]
    assert req.url.params["grant_type"] == "password"
    assert req.headers["apikey"] == ANON_KEY
    assert req.headers["authorization"] == f"Bearer {ANON_KEY}"
    assert sb.cuerpo() == {"email": "a@b.co", "password": "clave-larga-1"}
    assert tokens.access_token and tokens.refresh_token and tokens.expires_in == 3600


async def test_login_extrae_solo_los_factores_totp_verificados():
    sb = SupabaseSimulado()
    cuerpo = sesion_json(
        factores=[
            {"id": "f-no", "factor_type": "totp", "status": "unverified"},
            {"id": FACTOR, "factor_type": "totp", "status": "verified"},
            {"id": "f-tel", "factor_type": "phone", "status": "verified"},
        ]
    )
    sb.responder("POST", "/auth/v1/token", 200, cuerpo)
    tokens = await sb.cliente().login_password("a@b.co", "x")
    assert tokens.factores_verificados == (FACTOR,)


async def test_el_repr_de_los_tokens_no_los_expone():
    sb = SupabaseSimulado()
    cuerpo = sesion_json()
    sb.responder("POST", "/auth/v1/token", 200, cuerpo)
    tokens = await sb.cliente().login_password("a@b.co", "x")
    assert cuerpo["access_token"] not in repr(tokens)
    assert cuerpo["refresh_token"] not in repr(tokens)


@pytest.mark.parametrize("estado", [400, 401, 422])
async def test_login_con_credenciales_malas(estado):
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", estado, {"error": "invalid_grant", "error_description": "a@b.co"})
    with pytest.raises(CredencialesInvalidas) as e:
        await sb.cliente().login_password("a@b.co", "x")
    assert "a@b.co" not in str(e.value)


async def test_429_y_fallos_de_red_y_5xx_se_distinguen():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 429, {"msg": "rate"})
    with pytest.raises(LimiteExcedido):
        await sb.cliente().login_password("a@b.co", "x")
    sb.responder("POST", "/auth/v1/token", 503, {"msg": "caído"})
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().login_password("a@b.co", "x")
    sb.fallar_red("POST", "/auth/v1/token")
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().login_password("a@b.co", "x")


async def test_respuesta_de_sesion_malformada_es_auth_no_disponible():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 200, {"access_token": "x"})
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().login_password("a@b.co", "x")


async def test_refrescar_usa_grant_refresh_token():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 200, sesion_json())
    await sb.cliente().refrescar("refresco-viejo")
    assert sb.llamadas[0].url.params["grant_type"] == "refresh_token"
    assert sb.cuerpo() == {"refresh_token": "refresco-viejo"}


async def test_refresh_invalido_es_sesion_expirada():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/token", 400, {"error": "invalid_grant"})
    with pytest.raises(SesionExpirada):
        await sb.cliente().refrescar("x")


async def test_enrolar_totp_con_el_token_del_usuario():
    sb = SupabaseSimulado()
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "type": "totp", "totp": {"qr_code": "<svg/>", "secret": "ABC", "uri": "otpauth://totp/x"}},
    )
    e = await sb.cliente().enrolar_totp("acceso-usuario", "Panel")
    req = sb.llamadas[0]
    assert req.headers["authorization"] == "Bearer acceso-usuario"
    assert req.headers["apikey"] == ANON_KEY
    assert sb.cuerpo()["factor_type"] == "totp"
    assert (e.factor_id, e.secret, e.uri, e.qr_code) == (
        FACTOR,
        "ABC",
        "otpauth://totp/x",
        "data:image/svg+xml;charset=utf-8,%3Csvg/%3E",
    )


SVG_SUPABASE = '<svg xmlns="http://www.w3.org/2000/svg" fill="#fff" viewBox="0 0 29 29"><path d="M0 0h1"/></svg>'


async def test_enrolar_normaliza_el_svg_crudo_de_supabase_a_data_uri():
    from urllib.parse import unquote

    sb = SupabaseSimulado()
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "type": "totp", "totp": {"qr_code": SVG_SUPABASE, "secret": "ABC", "uri": "otpauth://x"}},
    )
    e = await sb.cliente().enrolar_totp("t", "Panel")
    prefijo = "data:image/svg+xml;charset=utf-8,"
    assert e.qr_code.startswith(prefijo)
    cuerpo = e.qr_code[len(prefijo) :]
    assert "#" not in cuerpo and '"' not in cuerpo and " " not in cuerpo and "<" not in cuerpo
    assert unquote(cuerpo) == SVG_SUPABASE


async def test_enrolar_deja_igual_un_data_uri():
    sb = SupabaseSimulado()
    uri = "data:image/svg+xml;utf8,%3Csvg%3E%3C/svg%3E"
    sb.responder(
        "POST",
        "/auth/v1/factors",
        200,
        {"id": FACTOR, "type": "totp", "totp": {"qr_code": uri, "secret": "ABC", "uri": "otpauth://x"}},
    )
    assert (await sb.cliente().enrolar_totp("t", "Panel")).qr_code == uri


async def test_enrolar_con_token_vencido_y_con_rechazo():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/factors", 401, {"msg": "jwt expired"})
    with pytest.raises(SesionExpirada):
        await sb.cliente().enrolar_totp("x", "Panel")
    sb.responder("POST", "/auth/v1/factors", 422, {"msg": "demasiados factores"})
    with pytest.raises(SolicitudRechazada):
        await sb.cliente().enrolar_totp("x", "Panel")


async def test_verificar_totp_hace_challenge_y_verify_y_devuelve_la_sesion_nueva():
    sb = SupabaseSimulado()
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "desafio-1"})
    nueva = sesion_json()
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", 200, nueva)
    tokens = await sb.cliente().verificar_totp("acceso-usuario", FACTOR, "123456")
    assert [r.url.path for r in sb.llamadas] == [
        f"/auth/v1/factors/{FACTOR}/challenge",
        f"/auth/v1/factors/{FACTOR}/verify",
    ]
    assert all(r.headers["authorization"] == "Bearer acceso-usuario" for r in sb.llamadas)
    assert sb.cuerpo() == {"challenge_id": "desafio-1", "code": "123456"}
    assert tokens.access_token == nueva["access_token"]


@pytest.mark.parametrize("estado", [400, 422])
async def test_codigo_totp_incorrecto(estado):
    sb = SupabaseSimulado()
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/challenge", 200, {"id": "d"})
    sb.responder("POST", f"/auth/v1/factors/{FACTOR}/verify", estado, {"error_code": "mfa_verification_failed"})
    with pytest.raises(CodigoMFAInvalido):
        await sb.cliente().verificar_totp("x", FACTOR, "000000")


async def test_factor_id_que_no_es_uuid_no_llega_a_la_url():
    sb = SupabaseSimulado()
    with pytest.raises(SolicitudRechazada):
        await sb.cliente().verificar_totp("x", "../../admin/users", "123456")
    assert sb.llamadas == []


async def test_aceptar_invitacion_verifica_el_hash_y_define_la_contrasena():
    sb = SupabaseSimulado()
    sesion = sesion_json()
    sb.responder("POST", "/auth/v1/verify", 200, sesion)
    sb.responder("PUT", "/auth/v1/user", 200, {"id": str(uuid.uuid4())})
    tokens = await sb.cliente().aceptar_invitacion("hash-de-prueba", "una-clave-nueva-123")
    verificar, definir = sb.llamadas
    assert verificar.headers["apikey"] == ANON_KEY
    assert b'"type":"invite"' in verificar.content.replace(b" ", b"")
    assert b"hash-de-prueba" in verificar.content
    assert definir.headers["authorization"] == f"Bearer {sesion['access_token']}"
    assert b"una-clave-nueva-123" in definir.content
    assert tokens.access_token == sesion["access_token"]


@pytest.mark.parametrize("estado", [400, 403, 404, 410, 422])
async def test_invitacion_vencida_o_invalida(estado):
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/verify", estado, {"error_code": "otp_expired"})
    with pytest.raises(InvitacionInvalida):
        await sb.cliente().aceptar_invitacion("x", "una-clave-nueva-123")
    assert len(sb.llamadas) == 1  # no se intenta definir la contraseña


async def test_contrasena_debil_se_distingue():
    sb = SupabaseSimulado()
    sb.responder("POST", "/auth/v1/verify", 200, sesion_json())
    sb.responder("PUT", "/auth/v1/user", 422, {"error_code": "weak_password"})
    with pytest.raises(ContrasenaDebil):
        await sb.cliente().aceptar_invitacion("x", "123")


async def test_cerrar_sesion_es_local_y_no_falla_si_supabase_no_responde():
    sb = SupabaseSimulado()
    sb.fallar_red("POST", "/auth/v1/logout")
    await sb.cliente().cerrar_sesion("acceso")  # no lanza
    assert sb.llamadas[0].url.params["scope"] == "local"
    assert sb.llamadas[0].headers["authorization"] == "Bearer acceso"


def test_constructor_rechaza_url_o_anon_key_vacias():
    with pytest.raises(ValueError):
        ClienteSupabaseAuth("", "k")
    with pytest.raises(ValueError):
        ClienteSupabaseAuth("https://x.test", "")


async def test_sin_cliente_http_inyectado_crea_uno_propio(monkeypatch):
    llamadas = []

    class _Falso(httpx.AsyncClient):
        async def request(self, *a, **k):
            llamadas.append((a, k))
            return httpx.Response(500, request=httpx.Request("POST", "https://x.test"))

    monkeypatch.setattr(httpx, "AsyncClient", _Falso)
    with pytest.raises(AuthNoDisponible):
        await ClienteSupabaseAuth("https://x.test", "k").login_password("a@b.co", "x")
    assert llamadas


# ---------------------------------------------------------------- factores verificados (GET /user)


async def test_factores_verificados_consulta_user_con_el_token_del_usuario():
    sb = SupabaseSimulado()
    sb.responder(
        "GET",
        "/auth/v1/user",
        200,
        {
            "id": FACTOR,
            "factors": [
                {"id": FACTOR, "factor_type": "totp", "status": "verified"},
                {"id": "otro", "factor_type": "totp", "status": "unverified"},
                {"id": "tel", "factor_type": "phone", "status": "verified"},
            ],
        },
    )
    assert await sb.cliente().factores_verificados("acceso-usuario") == (FACTOR,)
    assert sb.llamadas[0].headers["authorization"] == "Bearer acceso-usuario"


async def test_factores_verificados_sin_factores_es_vacio():
    sb = SupabaseSimulado()
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR})
    assert await sb.cliente().factores_verificados("t") == ()


async def test_factores_verificados_falla_cerrada():
    sb = SupabaseSimulado()
    sb.responder("GET", "/auth/v1/user", 401, {"msg": "x"})
    with pytest.raises(SesionExpirada):
        await sb.cliente().factores_verificados("t")
    sb.responder("GET", "/auth/v1/user", 404, {"msg": "x"})
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().factores_verificados("t")
    sb.responder("GET", "/auth/v1/user", 200, ["no es un objeto"])
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().factores_verificados("t")
    sb.responder("GET", "/auth/v1/user", 200, {"id": FACTOR, "factors": "raro"})
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().factores_verificados("t")
    sb.fallar_red("GET", "/auth/v1/user")
    with pytest.raises(AuthNoDisponible):
        await sb.cliente().factores_verificados("t")
