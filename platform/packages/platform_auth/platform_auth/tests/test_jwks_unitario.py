"""ProveedorJWKS: JWKS precargado, refrescado en segundo plano y sin red en la ruta del request."""

import asyncio
import json
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from platform_auth import ClavesNoDisponibles, NoAutenticado, VerificadorJWT
from platform_auth.jwks import ProveedorJWKS

AUD = "authenticated"
URL = "https://proyecto.supabase.test/auth/v1/.well-known/jwks.json"


def _par(kid):
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(privada.public_key()))
    publica.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return privada, publica


def _token(privada, kid):
    claims = {"sub": str(uuid.uuid4()), "aud": AUD, "exp": int(time.time()) + 300}
    return jwt.encode(claims, privada, algorithm="ES256", headers={"kid": kid})


class _Servidor:
    """Transporte httpx falso: cuenta las llamadas y sirve el JWKS actual (o falla)."""

    def __init__(self, claves=()):
        self.claves = list(claves)
        self.llamadas = 0
        self.falla = False

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.llamadas += 1
        if self.falla:
            return httpx.Response(500)
        return httpx.Response(200, json={"keys": self.claves})


def _proveedor(servidor, **kw):
    kw.setdefault("intervalo_seg", 3600)
    kw.setdefault("reintento_seg", 3600)
    kw.setdefault("min_entre_refrescos_kid_seg", 0)
    return ProveedorJWKS(URL, transporte=httpx.MockTransport(servidor), **kw)


async def _esperar(condicion, timeout=3.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("la condición no se cumplió a tiempo")


async def test_precarga_al_abrir_y_verifica_sin_mas_red():
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    proveedor = _proveedor(servidor)
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=proveedor)
    await verificador.abrir()
    try:
        assert servidor.llamadas == 1
        for _ in range(5):
            verificador.verificar(_token(privada, "k1"))
        assert servidor.llamadas == 1
    finally:
        await verificador.cerrar()


async def test_kid_desconocido_da_401_sin_http_y_el_loop_sigue_respondiendo():
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    # min_entre_refrescos alto: ningún kid aleatorio debe provocar una descarga.
    proveedor = _proveedor(servidor, min_entre_refrescos_kid_seg=3600)
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=proveedor)
    await verificador.abrir()
    try:
        llamadas_antes = servidor.llamadas
        latidos = 0

        async def latido():
            nonlocal latidos
            while True:
                await asyncio.sleep(0.005)
                latidos += 1

        tarea = asyncio.create_task(latido())
        for _ in range(50):
            with pytest.raises(NoAutenticado):
                verificador.verificar(_token(privada, uuid.uuid4().hex))
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        tarea.cancel()
        assert servidor.llamadas == llamadas_antes
        assert latidos > 0
    finally:
        await verificador.cerrar()


async def test_token_sin_kid_es_401():
    privada, publica = _par("k1")
    proveedor = _proveedor(_Servidor([publica]))
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=proveedor)
    await verificador.abrir()
    try:
        sin_kid = jwt.encode({"sub": str(uuid.uuid4()), "aud": AUD, "exp": int(time.time()) + 60}, privada, "ES256")
        with pytest.raises(NoAutenticado):
            verificador.verificar(sin_kid)
    finally:
        await verificador.cerrar()


async def test_rotacion_un_kid_nuevo_se_incorpora_en_segundo_plano():
    p1, pub1 = _par("k1")
    p2, pub2 = _par("k2")
    servidor = _Servidor([pub1])
    proveedor = _proveedor(servidor)
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=proveedor)
    await verificador.abrir()
    try:
        servidor.claves = [pub1, pub2]  # Supabase rota: aparece k2
        with pytest.raises(NoAutenticado):  # el primer token con k2 falla sin red...
            verificador.verificar(_token(p2, "k2"))
        espera = time.monotonic() + 3
        while time.monotonic() < espera:  # ...y al rato el refresco de fondo ya lo conoce
            await asyncio.sleep(0.01)
            try:
                verificador.verificar(_token(p2, "k2"))
                break
            except NoAutenticado:
                continue
        else:
            raise AssertionError("k2 nunca se incorporó")
        verificador.verificar(_token(p1, "k1"))
    finally:
        await verificador.cerrar()


async def test_refresco_por_kid_desconocido_tiene_limite_de_frecuencia():
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    proveedor = _proveedor(servidor, min_entre_refrescos_kid_seg=3600)
    await proveedor.abrir()
    try:
        for _ in range(20):
            with pytest.raises(jwt.PyJWTError):
                proveedor.get_signing_key_from_jwt(_token(privada, uuid.uuid4().hex))
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        assert servidor.llamadas == 1
    finally:
        await proveedor.cerrar()


async def test_refresco_periodico_en_segundo_plano():
    p1, pub1 = _par("k1")
    p2, pub2 = _par("k2")
    servidor = _Servidor([pub1])
    proveedor = _proveedor(servidor, intervalo_seg=0.05)
    await proveedor.abrir()
    try:
        servidor.claves = [pub2]
        await _esperar(lambda: servidor.llamadas >= 3)
        assert proveedor.get_signing_key_from_jwt(_token(p2, "k2")) is not None
    finally:
        await proveedor.cerrar()


async def test_si_el_refresco_falla_se_conserva_el_ultimo_jwks_y_se_loguea_sin_datos(caplog):
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    proveedor = _proveedor(servidor, intervalo_seg=0.05)
    await proveedor.abrir()
    try:
        servidor.falla = True
        with caplog.at_level("WARNING", logger="platform_auth.jwks"):
            await _esperar(lambda: servidor.llamadas >= 3)
        assert proveedor.get_signing_key_from_jwt(_token(privada, "k1")) is not None
        assert any("jwks_refresco_fallido" in r.getMessage() for r in caplog.records)
        assert all(URL not in r.getMessage() for r in caplog.records)
    finally:
        await proveedor.cerrar()


async def test_arranque_con_jwks_caido_arranca_responde_503_y_se_recupera():
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    servidor.falla = True
    proveedor = _proveedor(servidor, reintento_seg=0.05)
    verificador = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=proveedor)
    await verificador.abrir()  # no lanza ni bloquea
    try:
        with pytest.raises(ClavesNoDisponibles) as info:
            verificador.verificar(_token(privada, "k1"))
        assert info.value.status_code == 503
        servidor.falla = False
        await _esperar(lambda: proveedor.tiene_claves)
        verificador.verificar(_token(privada, "k1"))
    finally:
        await verificador.cerrar()


async def test_cerrar_cancela_la_tarea_de_fondo():
    _, publica = _par("k1")
    servidor = _Servidor([publica])
    proveedor = _proveedor(servidor, intervalo_seg=0.02)
    await proveedor.abrir()
    await proveedor.cerrar()
    llamadas = servidor.llamadas
    await asyncio.sleep(0.1)
    assert servidor.llamadas == llamadas
    assert proveedor._tarea is None


async def test_jwks_con_claves_invalidas_no_reemplaza_el_anterior():
    privada, publica = _par("k1")
    servidor = _Servidor([publica])
    proveedor = _proveedor(servidor)
    await proveedor.abrir()
    try:
        servidor.claves = [{"kty": "oct", "kid": "x"}, {"sin": "kid"}]
        with pytest.raises(ValueError):
            await proveedor.refrescar()
        assert proveedor.get_signing_key_from_jwt(_token(privada, "k1")) is not None
    finally:
        await proveedor.cerrar()
