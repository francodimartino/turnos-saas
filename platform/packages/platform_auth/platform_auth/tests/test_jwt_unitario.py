"""Verificación de JWT de Supabase Auth: firma, expiración, audiencia y claims (§3.4). Tokens firmados en el test."""

import secrets
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from platform_auth import ConfigJWTInvalida, NoAutenticado, VerificadorJWT

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)  # efímero: se genera en cada corrida


class _ClaveJWKS:
    """Imita a PyJWKClient: devuelve la clave pública, sin red."""

    def __init__(self, clave_publica):
        self._clave = clave_publica

    def get_signing_key_from_jwt(self, token):
        clave = self._clave

        class _K:
            key = clave

        return _K()


def _claims(**extra):
    """Claims válidos; un valor `...` quita el claim."""
    base = {
        "sub": str(uuid.uuid4()),
        "aud": AUD,
        "exp": int(time.time()) + 300,
        "aal": "aal1",
        "tenant_id": str(uuid.uuid4()),
        "is_owner": False,
        "is_platform_admin": False,
    }
    base.update(extra)
    return {k: v for k, v in base.items() if v is not ...}


@pytest.fixture(scope="module")
def clave_ec():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def clave_rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verificador_ec(clave_ec):
    return VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=_ClaveJWKS(clave_ec.public_key()))


def test_token_valido_es256_extrae_claims(verificador_ec, clave_ec):
    claims = _claims(is_owner=True, aal="aal2")
    ident = verificador_ec.verificar(jwt.encode(claims, clave_ec, algorithm="ES256"))
    assert str(ident.user_id) == claims["sub"]
    assert str(ident.tenant_id) == claims["tenant_id"]
    assert ident.is_owner is True
    assert ident.is_platform_admin is False
    assert ident.aal == "aal2"


def test_is_platform_admin_true_se_lee_del_claim(verificador_ec, clave_ec):
    ident = verificador_ec.verificar(jwt.encode(_claims(is_platform_admin=True), clave_ec, algorithm="ES256"))
    assert ident.is_platform_admin is True
    # Es independiente de is_owner: la clave leída es is_platform_admin, no otra.
    assert ident.is_owner is False


@pytest.mark.parametrize("valor", [False, ..., "true", "false", 1, 0, None, [], {}])
def test_is_platform_admin_solo_true_json_otorga_privilegio(verificador_ec, clave_ec, valor):
    token = jwt.encode(_claims(is_platform_admin=valor), clave_ec, algorithm="ES256")
    assert verificador_ec.verificar(token).is_platform_admin is False


def test_is_owner_true_no_activa_is_platform_admin(verificador_ec, clave_ec):
    ident = verificador_ec.verificar(jwt.encode(_claims(is_owner=True), clave_ec, algorithm="ES256"))
    assert ident.is_owner is True and ident.is_platform_admin is False


def test_token_valido_rs256(clave_rsa):
    v = VerificadorJWT(algoritmos=["RS256"], audiencia=AUD, proveedor_clave=_ClaveJWKS(clave_rsa.public_key()))
    ident = v.verificar(jwt.encode(_claims(), clave_rsa, algorithm="RS256"))
    assert ident.tenant_id is not None


def test_token_valido_hs256_con_secreto():
    v = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)
    ident = v.verificar(jwt.encode(_claims(), SECRETO, algorithm="HS256"))
    assert ident.aal == "aal1"


def test_tenant_id_null_se_extrae_como_none(verificador_ec, clave_ec):
    # El Auth Hook emite tenant_id null para Franco o staff desactivado: la identidad existe y la
    # decisión de 401 para dashboard-backend es de la capa de sesión.
    token = jwt.encode(_claims(tenant_id=None), clave_ec, algorithm="ES256")
    assert verificador_ec.verificar(token).tenant_id is None


def test_claims_ausentes_toman_valores_seguros(verificador_ec, clave_ec):
    token = jwt.encode(
        _claims(tenant_id=..., is_owner=..., is_platform_admin=..., aal=...), clave_ec, algorithm="ES256"
    )
    ident = verificador_ec.verificar(token)
    assert (ident.tenant_id, ident.is_owner, ident.is_platform_admin, ident.aal) == (None, False, False, None)


def test_claims_booleanos_no_booleanos_no_otorgan_privilegio(verificador_ec, clave_ec):
    # "false" (string) es truthy en Python: solo un `true` JSON cuenta como verdadero.
    token = jwt.encode(_claims(is_owner="false", is_platform_admin=1), clave_ec, algorithm="ES256")
    ident = verificador_ec.verificar(token)
    assert ident.is_owner is False and ident.is_platform_admin is False


def test_expirado_es_401(verificador_ec, clave_ec):
    token = jwt.encode(_claims(exp=int(time.time()) - 10), clave_ec, algorithm="ES256")
    with pytest.raises(NoAutenticado) as e:
        verificador_ec.verificar(token)
    assert e.value.status_code == 401


def test_sin_exp_es_401(verificador_ec, clave_ec):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(exp=...), clave_ec, algorithm="ES256"))


def test_audiencia_incorrecta_es_401(verificador_ec, clave_ec):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(aud="otra"), clave_ec, algorithm="ES256"))


def test_sin_audiencia_es_401(verificador_ec, clave_ec):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(aud=...), clave_ec, algorithm="ES256"))


def test_firma_de_otra_clave_es_401(verificador_ec):
    otra = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(), otra, algorithm="ES256"))


def test_hs256_con_secreto_equivocado_es_401():
    v = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)
    otro = secrets.token_urlsafe(48)
    with pytest.raises(NoAutenticado):
        v.verificar(jwt.encode(_claims(), otro, algorithm="HS256"))


def test_alg_none_es_401(verificador_ec):
    token = jwt.encode(_claims(), None, algorithm="none")
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(token)


def test_confusion_de_algoritmo_es_401(verificador_ec):
    # Verificador asimétrico: un HS256 no está en su lista de algoritmos y se rechaza.
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(), SECRETO, algorithm="HS256"))


@pytest.mark.parametrize("basura", ["", "abc", "a.b.c", "Bearer x"])
def test_token_malformado_es_401(verificador_ec, basura):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(basura)


def test_sub_que_no_es_uuid_es_401(verificador_ec, clave_ec):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(sub="no-es-uuid"), clave_ec, algorithm="ES256"))


def test_tenant_id_que_no_es_uuid_es_401(verificador_ec, clave_ec):
    with pytest.raises(NoAutenticado):
        verificador_ec.verificar(jwt.encode(_claims(tenant_id="xyz"), clave_ec, algorithm="ES256"))


def test_emisor_configurado_se_exige(clave_ec):
    v = VerificadorJWT(
        algoritmos=["ES256"],
        audiencia=AUD,
        emisor="https://x.supabase.co/auth/v1",
        proveedor_clave=_ClaveJWKS(clave_ec.public_key()),
    )
    with pytest.raises(NoAutenticado):
        v.verificar(jwt.encode(_claims(iss="https://otro"), clave_ec, algorithm="ES256"))
    assert v.verificar(jwt.encode(_claims(iss="https://x.supabase.co/auth/v1"), clave_ec, algorithm="ES256"))


def test_jwks_inalcanzable_es_401_no_500(clave_ec):
    class _Roto:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("sin red")

    v = VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, proveedor_clave=_Roto())
    with pytest.raises(NoAutenticado):
        v.verificar(jwt.encode(_claims(), clave_ec, algorithm="ES256"))


def test_constructor_exige_exactamente_una_fuente_de_clave():
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["HS256"], audiencia=AUD)
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO, proveedor_clave=object())


def test_constructor_rechaza_none_y_mezcla_de_familias():
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["none"], audiencia=AUD, secreto=SECRETO)
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["HS256", "ES256"], audiencia=AUD, secreto=SECRETO)
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, proveedor_clave=object())
    with pytest.raises(ConfigJWTInvalida):
        VerificadorJWT(algoritmos=["ES256"], audiencia=AUD, secreto=SECRETO)


class TestDesdeEntorno:
    def test_secreto_hs256(self):
        v = VerificadorJWT.desde_entorno({"SUPABASE_JWT_SECRET": SECRETO})
        assert v.verificar(jwt.encode(_claims(), SECRETO, algorithm="HS256")).aal == "aal1"

    def test_jwks_url_arma_cliente_asimetrico(self):
        url = "https://x.supabase.co/auth/v1/.well-known/jwks.json"
        v = VerificadorJWT.desde_entorno({"SUPABASE_JWKS_URL": url})
        assert "ES256" in v.algoritmos and "HS256" not in v.algoritmos

    def test_audiencia_y_emisor_por_entorno(self):
        v = VerificadorJWT.desde_entorno(
            {"SUPABASE_JWT_SECRET": SECRETO, "SUPABASE_JWT_AUDIENCE": "otra", "SUPABASE_JWT_ISSUER": "iss"}
        )
        assert v.audiencia == "otra" and v.emisor == "iss"

    def test_audiencia_por_defecto_es_authenticated(self):
        assert VerificadorJWT.desde_entorno({"SUPABASE_JWT_SECRET": SECRETO}).audiencia == "authenticated"

    def test_sin_configuracion_falla(self):
        with pytest.raises(ConfigJWTInvalida):
            VerificadorJWT.desde_entorno({})

    def test_ambas_fuentes_falla(self):
        with pytest.raises(ConfigJWTInvalida):
            VerificadorJWT.desde_entorno({"SUPABASE_JWT_SECRET": SECRETO, "SUPABASE_JWKS_URL": "https://x/jwks"})

    def test_jwks_url_no_https_falla_salvo_loopback(self):
        with pytest.raises(ConfigJWTInvalida):
            VerificadorJWT.desde_entorno({"SUPABASE_JWKS_URL": "http://x.example.com/jwks"})
        local = "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"
        assert VerificadorJWT.desde_entorno({"SUPABASE_JWKS_URL": local})
