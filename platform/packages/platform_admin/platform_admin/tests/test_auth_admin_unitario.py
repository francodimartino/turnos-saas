"""El cliente HTTP de la Auth Admin API (mockeado) y el aislamiento de la credencial `service_role`."""

import inspect
import json
import pathlib
import uuid

import httpx
import pytest
from platform_auth import SupabaseAuthAdmin

import platform_admin
from platform_admin import ErrorInvitacion
from platform_admin.tests.test_tenants_unitario import (
    DATOS,
    EMAIL,
    PoolFalso,
    _conn_creacion,
)

# Valor de prueba construido en runtime: no es un token real ni queda literal en el repo.
CLAVE = "clave-de-prueba-" + uuid.uuid4().hex
URL = "https://proyecto.example.test"
USUARIO = uuid.uuid4()


def _cliente(handler):
    return SupabaseAuthAdmin(URL, CLAVE, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_invitar_hace_post_a_la_auth_admin_api_y_devuelve_el_id():
    vistos = []

    def handler(request: httpx.Request):
        vistos.append(request)
        return httpx.Response(200, json={"id": str(USUARIO), "email": EMAIL})

    assert await _cliente(handler).invitar(EMAIL) == USUARIO
    (req,) = vistos
    assert req.method == "POST" and str(req.url) == f"{URL}/auth/v1/invite"
    assert req.headers["authorization"] == f"Bearer {CLAVE}" and req.headers["apikey"] == CLAVE
    assert json.loads(req.content) == {"email": EMAIL}


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, text="x"),
        httpx.Response(422, json={"msg": "ya existe"}),
        httpx.Response(200, json={"sin": "id"}),
        httpx.Response(200, text="no json"),
    ],
)
async def test_invitar_respuesta_mala_es_error_de_invitacion_sin_filtrar_secretos(respuesta):
    with pytest.raises(ErrorInvitacion) as exc:
        await _cliente(lambda r: respuesta).invitar(EMAIL)
    assert CLAVE not in str(exc.value) and EMAIL not in str(exc.value)


async def test_invitar_error_de_red_es_error_de_invitacion():
    def handler(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorInvitacion) as exc:
        await _cliente(handler).invitar(EMAIL)
    assert CLAVE not in str(exc.value)


def test_desde_entorno_lee_las_variables_y_falla_sin_ellas():
    cliente = SupabaseAuthAdmin.desde_entorno({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": CLAVE})
    assert CLAVE not in repr(cliente) and CLAVE not in str(cliente)
    with pytest.raises(ValueError):
        SupabaseAuthAdmin.desde_entorno({"SUPABASE_URL": URL})
    with pytest.raises(ValueError):
        SupabaseAuthAdmin.desde_entorno({"SUPABASE_SERVICE_ROLE_KEY": CLAVE})


def test_service_role_solo_aparece_en_el_modulo_de_la_auth_admin_api():
    paquetes = pathlib.Path(platform_admin.__file__).parents[2]
    lectores = sorted(
        p.relative_to(paquetes).as_posix()
        for p in paquetes.glob("*/*/*.py")
        if "SUPABASE_SERVICE_ROLE_KEY" in p.read_text(encoding="utf-8")
    )
    assert lectores == ["platform_auth/platform_auth/auth_admin.py"]


def test_solo_hay_una_clase_que_habla_con_la_auth_admin_api():
    """Ningún otro módulo de código (platform, apps, scripts) arma las rutas /auth/v1/invite ni /auth/v1/admin."""
    raiz = pathlib.Path(platform_admin.__file__).parents[4]
    clientes = sorted(
        p.relative_to(raiz).as_posix()
        for carpeta in ("platform", "apps")
        for p in (raiz / carpeta).rglob("*.py")
        if "tests" not in p.parts
        and "node_modules" not in p.parts
        and any(ruta in p.read_text(encoding="utf-8") for ruta in ("/auth/v1/invite", "/auth/v1/admin"))
    )
    assert clientes == ["platform/packages/platform_auth/platform_auth/auth_admin.py"]


async def test_ninguna_otra_ruta_recibe_la_credencial():
    """create_tenant solo pasa el email al cliente; el pool y la base nunca ven la clave."""
    recibidos = []

    class Espia:
        async def invitar(self, email, nombre=None):
            recibidos.append(email)
            return USUARIO

    conn = _conn_creacion()
    await platform_admin.create_tenant(
        PoolFalso(conn), Espia(), DATOS, EMAIL, owner_nombre="Ana", actor_id=uuid.uuid4()
    )
    assert recibidos == [EMAIL]
    assert CLAVE not in repr(conn.comandos)
    # las funciones de ciclo de vida y de lectura ni siquiera reciben un cliente de Auth
    for nombre in ("suspender", "levantar_suspension", "dar_de_baja", "reactivar", "listar_tenants", "obtener_tenant"):
        assert "cliente" not in inspect.signature(getattr(platform_admin, nombre)).parameters


async def test_invitar_envia_el_nombre_como_user_metadata_full_name():
    vistos = []

    def handler(request: httpx.Request):
        vistos.append(request)
        return httpx.Response(200, json={"id": str(USUARIO)})

    await _cliente(handler).invitar(EMAIL, "Ana Pérez")
    assert json.loads(vistos[0].content) == {"email": EMAIL, "data": {"full_name": "Ana Pérez"}}
