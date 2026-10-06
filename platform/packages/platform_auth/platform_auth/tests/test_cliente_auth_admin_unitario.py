"""Cliente HTTP de la Auth Admin API de Supabase, con el transporte de httpx simulado (sin red ni credenciales)."""

import json
import secrets
import uuid

import httpx
import pytest

from platform_auth import ErrorInvitacion, SupabaseAuthAdmin

URL = "https://proyecto.invalid"
CLAVE = secrets.token_urlsafe(32)  # efímera: se genera en cada corrida
EMAIL = "persona@ejemplo.invalid"


def _cliente(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseAuthAdmin(URL, CLAVE, http=http, **kw)


async def test_invita_con_service_role_y_devuelve_el_id():
    nuevo = uuid.uuid4()
    vistos = []

    def handler(request: httpx.Request):
        vistos.append(request)
        return httpx.Response(200, json={"id": str(nuevo), "email": EMAIL})

    assert await _cliente(handler).invitar_por_email(EMAIL) == nuevo
    req = vistos[0]
    assert req.method == "POST"
    assert str(req.url) == f"{URL}/auth/v1/invite"
    assert req.headers["authorization"] == f"Bearer {CLAVE}"
    assert req.headers["apikey"] == CLAVE
    assert json.loads(req.content) == {"email": EMAIL}


async def test_redirect_to_va_como_query():
    vistos = []

    def handler(request):
        vistos.append(request)
        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    await _cliente(handler, redirect_to="https://app.invalid/aceptar").invitar_por_email(EMAIL)
    assert vistos[0].url.params["redirect_to"] == "https://app.invalid/aceptar"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_errores_transitorios_son_reintentables(status):
    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(lambda r: httpx.Response(status, json={"msg": "x"})).invitar_por_email(EMAIL)
    assert e.value.reintentable is True


async def test_fallo_de_red_es_reintentable():
    def handler(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(handler).invitar_por_email(EMAIL)
    assert e.value.reintentable is True


async def test_timeout_es_reintentable():
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(handler).invitar_por_email(EMAIL)
    assert e.value.reintentable is True


@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_errores_permanentes_no_son_reintentables(status):
    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(lambda r: httpx.Response(status, json={"msg": "x"})).invitar_por_email(EMAIL)
    assert e.value.reintentable is False


async def test_respuesta_sin_id_valido_es_error_no_reintentable():
    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(lambda r: httpx.Response(200, json={"id": "no-es-uuid"})).invitar_por_email(EMAIL)
    assert e.value.reintentable is False


async def test_los_errores_no_filtran_la_clave_ni_el_email():
    def handler(request):
        return httpx.Response(500, text=f"fallo con {EMAIL} y {CLAVE}")

    with pytest.raises(ErrorInvitacion) as e:
        await _cliente(handler).invitar_por_email(EMAIL)
    assert CLAVE not in str(e.value)
    assert EMAIL not in str(e.value)


def test_desde_entorno_lee_url_y_clave(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL + "/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", CLAVE)
    assert SupabaseAuthAdmin.desde_entorno().base_url == URL


def test_desde_entorno_sin_variables_falla(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError):
        SupabaseAuthAdmin.desde_entorno()


def test_repr_no_expone_la_clave():
    assert CLAVE not in repr(SupabaseAuthAdmin(URL, CLAVE))


async def test_el_nombre_viaja_como_user_metadata_full_name():
    vistos = []

    def handler(request):
        vistos.append(request)
        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    await _cliente(handler).invitar_por_email(EMAIL, "Ana Pérez")
    assert json.loads(vistos[0].content) == {"email": EMAIL, "data": {"full_name": "Ana Pérez"}}
