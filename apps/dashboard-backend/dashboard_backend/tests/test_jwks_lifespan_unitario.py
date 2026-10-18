"""El lifespan de la app abre y cierra el JWKS; mientras no haya claves un token da 503, no 401 ni bloqueo."""

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from platform_auth import VerificadorJWT
from platform_auth.jwks import ProveedorJWKS

from dashboard_backend.app import crear_app
from dashboard_backend.tests.soporte import PoolFalso, config_prueba, http

_TOKEN = jwt.encode({"sub": "x"}, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256", headers={"kid": "k1"})


class _Transporte(httpx.AsyncBaseTransport):
    def __init__(self):
        self.llamadas = 0

    async def handle_async_request(self, request):
        self.llamadas += 1
        return httpx.Response(500)


def _app(verificador):
    return crear_app(
        config_prueba(),
        verificador=verificador,
        pool=PoolFalso(),
        configurar_observabilidad=False,
    )


async def test_lifespan_abre_y_cierra_el_jwks_y_sin_claves_responde_503():
    transporte = _Transporte()
    proveedor = ProveedorJWKS("https://jwks.ejemplo.test/jwks.json", transporte=transporte, reintento_seg=3600)
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia="authenticated", proveedor_clave=proveedor)
    app = _app(verificador)
    async with app.router.lifespan_context(app):
        assert transporte.llamadas == 1  # precargado al arrancar, aunque falle: la app arranca igual
        assert proveedor._tarea is not None
        async with http(app) as c:
            r = await c.get("/servicios", headers={"Authorization": "Bearer " + _TOKEN})
        assert r.status_code == 503
        assert r.json()["detail"]["codigo"] == "auth_no_disponible"
        assert transporte.llamadas == 1  # el request no hizo red
    assert proveedor._tarea is None
