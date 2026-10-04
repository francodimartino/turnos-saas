"""Verificación de JWT de Supabase Auth (§3.4): firma, expiración, audiencia (y emisor, si se configura).

La clave sale de la configuración por entorno, nunca del código:
  - `SUPABASE_JWKS_URL`: claves asimétricas (proyectos nuevos y hosteados), se descargan y cachean por JWKS.
  - `SUPABASE_JWT_SECRET`: secreto HS256 (Supabase local / proyectos con secreto legado).
  Exactamente una de las dos. `SUPABASE_JWT_AUDIENCE` (por defecto `authenticated`) y `SUPABASE_JWT_ISSUER`
  (opcional) completan la validación.

La lista de algoritmos es fija por modo y nunca incluye `none`: un token firmado con otra familia de algoritmo
(ataque de confusión HS256 con la clave pública) se rechaza.
"""

import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import jwt

from platform_auth.errores import ConfigJWTInvalida, NoAutenticado
from platform_auth.identidad import IdentidadJWT

_SIMETRICOS = frozenset({"HS256", "HS384", "HS512"})
_ASIMETRICOS = frozenset({"ES256", "ES384", "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "EdDSA"})
_ASIMETRICOS_POR_DEFECTO = ("ES256", "RS256", "EdDSA")
_HOSTS_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _uuid_o_none(valor: Any) -> uuid.UUID | None:
    if valor is None:
        return None
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise NoAutenticado() from None


class VerificadorJWT:
    def __init__(
        self,
        *,
        algoritmos: list[str] | tuple[str, ...],
        audiencia: str,
        secreto: str | None = None,
        proveedor_clave: Any = None,
        emisor: str | None = None,
    ):
        if (secreto is None) == (proveedor_clave is None):
            raise ConfigJWTInvalida("Se necesita exactamente una fuente de clave: secreto HS256 o JWKS")
        algoritmos = tuple(algoritmos)
        if not algoritmos:
            raise ConfigJWTInvalida("Lista de algoritmos vacía")
        familia = _SIMETRICOS if secreto is not None else _ASIMETRICOS
        if any(a not in familia for a in algoritmos):
            raise ConfigJWTInvalida(
                "Algoritmos no permitidos para esta fuente de clave "
                "(el secreto solo admite HS*, el JWKS solo asimétricos)"
            )
        if not audiencia:
            raise ConfigJWTInvalida("La audiencia no puede ser vacía")
        self.algoritmos = algoritmos
        self.audiencia = audiencia
        self.emisor = emisor or None
        self._secreto = secreto
        self._proveedor = proveedor_clave

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "VerificadorJWT":
        secreto = entorno.get("SUPABASE_JWT_SECRET") or None
        url = entorno.get("SUPABASE_JWKS_URL") or None
        audiencia = entorno.get("SUPABASE_JWT_AUDIENCE") or "authenticated"
        emisor = entorno.get("SUPABASE_JWT_ISSUER") or None
        if secreto and url:
            raise ConfigJWTInvalida("Definí SUPABASE_JWT_SECRET o SUPABASE_JWKS_URL, no ambas")
        if secreto:
            return cls(algoritmos=["HS256"], audiencia=audiencia, secreto=secreto, emisor=emisor)
        if url:
            partes = urlsplit(url)
            if partes.scheme != "https" and not (partes.scheme == "http" and partes.hostname in _HOSTS_LOOPBACK):
                raise ConfigJWTInvalida("SUPABASE_JWKS_URL debe ser https (http solo hacia loopback)")
            cliente = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
            return cls(
                algoritmos=_ASIMETRICOS_POR_DEFECTO, audiencia=audiencia, proveedor_clave=cliente, emisor=emisor
            )
        raise ConfigJWTInvalida("Falta SUPABASE_JWT_SECRET o SUPABASE_JWKS_URL")

    def _clave(self, token: str) -> Any:
        if self._secreto is not None:
            return self._secreto
        return self._proveedor.get_signing_key_from_jwt(token).key

    def verificar(self, token: str) -> IdentidadJWT:
        """Devuelve la identidad del token o lanza `NoAutenticado` (401). Nunca otra excepción por un token malo."""
        try:
            claims = jwt.decode(
                token,
                self._clave(token),
                algorithms=list(self.algoritmos),
                audience=self.audiencia,
                issuer=self.emisor,
                options={"require": ["exp", "sub", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            # Incluye firma, expiración, audiencia, emisor, formato y JWKS inalcanzable.
            raise NoAutenticado() from None

        user_id = _uuid_o_none(claims.get("sub"))
        if user_id is None:
            raise NoAutenticado()
        aal = claims.get("aal")
        return IdentidadJWT(
            user_id=user_id,
            tenant_id=_uuid_o_none(claims.get("tenant_id")),
            # Solo un `true` JSON otorga privilegio: "false" o 1 no.
            is_owner=claims.get("is_owner") is True,
            is_platform_admin=claims.get("is_platform_admin") is True,
            aal=aal if isinstance(aal, str) else None,
        )
