"""Eliminación de cuentas por la Auth Admin API (mockeada): el 404 cuenta como éxito, el resto es error."""

import uuid

import httpx
import pytest

from platform_admin import ClienteAuthAdminHttp, ErrorEliminacionCuenta

CLAVE = "clave-de-prueba-" + uuid.uuid4().hex
URL = "https://proyecto.example.test"
USUARIO = uuid.uuid4()


def _cliente(handler):
    return ClienteAuthAdminHttp(URL, CLAVE, transport=httpx.MockTransport(handler))


async def test_eliminar_hace_delete_al_usuario_con_la_credencial():
    vistos = []

    def handler(request: httpx.Request):
        vistos.append(request)
        return httpx.Response(200, json={})

    assert await _cliente(handler).eliminar_usuario(USUARIO) is True
    (req,) = vistos
    assert req.method == "DELETE" and str(req.url) == f"{URL}/auth/v1/admin/users/{USUARIO}"
    assert req.headers["authorization"] == f"Bearer {CLAVE}" and req.headers["apikey"] == CLAVE


async def test_eliminar_un_404_cuenta_como_exito_y_avisa_que_ya_no_existia():
    assert (
        await _cliente(lambda r: httpx.Response(404, json={"msg": "User not found"})).eliminar_usuario(USUARIO) is False
    )


@pytest.mark.parametrize("estado", [400, 401, 403, 422, 500, 503])
async def test_eliminar_otro_error_http_es_error_sin_filtrar_la_credencial(estado):
    with pytest.raises(ErrorEliminacionCuenta) as exc:
        await _cliente(lambda r: httpx.Response(estado, text=CLAVE)).eliminar_usuario(USUARIO)
    assert CLAVE not in str(exc.value) and str(estado) in str(exc.value)


async def test_eliminar_error_de_red_es_error_sin_filtrar_la_credencial():
    def handler(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(ErrorEliminacionCuenta) as exc:
        await _cliente(handler).eliminar_usuario(USUARIO)
    assert CLAVE not in str(exc.value)


async def test_eliminar_exige_un_uuid():
    with pytest.raises(ValueError):
        await _cliente(lambda r: httpx.Response(200)).eliminar_usuario("../admin")  # type: ignore[arg-type]
