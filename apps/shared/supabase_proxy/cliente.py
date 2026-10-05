"""Proxy de Supabase Auth (GoTrue REST) para login, MFA TOTP, invitación y logout (ARCHITECTURE.md §3.1.5).

El navegador nunca habla con Supabase: lo hace este cliente, con la **anon key** (nunca la service_role) y, cuando
la operación es de un usuario, con su access token. Se inyecta en la app (`crear_app(cliente_auth=...)`) para
probar con respuestas simuladas.

Los errores de Supabase se traducen a errores propios con mensajes genéricos: el cuerpo de la respuesta (que puede
traer el email del usuario o detalles internos) no se propaga ni se loguea.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

_log = logging.getLogger("supabase_proxy.cliente")
_EXPIRADO = (400, 401, 403, 422)


class ErrorSupabaseAuth(Exception):
    """Base: lleva el status HTTP y el código con que se responde al navegador."""

    status_http = 502
    codigo = "auth_no_disponible"
    mensaje = "El servicio de autenticación no está disponible"

    def __init__(self, mensaje: str | None = None):
        super().__init__(mensaje or self.mensaje)
        if mensaje is not None:
            self.mensaje = mensaje


class CredencialesInvalidas(ErrorSupabaseAuth):
    status_http = 401
    codigo = "credenciales_invalidas"
    mensaje = "Email o contraseña incorrectos"


class SesionExpirada(ErrorSupabaseAuth):
    status_http = 401
    codigo = "no_autenticado"
    mensaje = "Sesión inválida o expirada"


class CodigoMFAInvalido(ErrorSupabaseAuth):
    status_http = 401
    codigo = "codigo_mfa_invalido"
    mensaje = "El código de verificación es incorrecto o venció"


class InvitacionInvalida(ErrorSupabaseAuth):
    status_http = 400
    codigo = "invitacion_invalida"
    mensaje = "La invitación es inválida o venció"


class ContrasenaDebil(ErrorSupabaseAuth):
    status_http = 422
    codigo = "contrasena_debil"
    mensaje = "La contraseña no cumple los requisitos de seguridad"


class SolicitudRechazada(ErrorSupabaseAuth):
    status_http = 400
    codigo = "solicitud_rechazada"
    mensaje = "No se pudo completar la operación"


class LimiteExcedido(ErrorSupabaseAuth):
    status_http = 429
    codigo = "demasiados_intentos"
    mensaje = "Demasiados intentos: esperá un momento y probá de nuevo"


class AuthNoDisponible(ErrorSupabaseAuth):
    status_http = 502


@dataclass(frozen=True)
class TokensSesion:
    """Sesión emitida por Supabase. Los tokens no aparecen en `repr` (ni en logs ni en trazas de Sentry)."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    factores_verificados: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnrolamientoTOTP:
    factor_id: str
    qr_code: str = field(repr=False)
    secret: str = field(repr=False)
    uri: str = field(repr=False)


def _uuid_texto(valor: str) -> str:
    try:
        return str(uuid.UUID(str(valor)))
    except ValueError:
        raise SolicitudRechazada("El identificador del factor no es válido") from None


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        cuerpo = r.json()
    except ValueError:
        raise AuthNoDisponible() from None
    if not isinstance(cuerpo, dict):
        raise AuthNoDisponible()
    return cuerpo


def _codigo_error(r: httpx.Response) -> str:
    """`error_code` de GoTrue si lo hay (sirve para distinguir, nunca se devuelve ni se loguea el cuerpo)."""
    try:
        cuerpo = r.json()
    except ValueError:
        return ""
    if isinstance(cuerpo, dict):
        return str(cuerpo.get("error_code") or cuerpo.get("error") or "")
    return ""


def _sesion(r: httpx.Response) -> TokensSesion:
    cuerpo = _json(r)
    acceso, refresco, expira = cuerpo.get("access_token"), cuerpo.get("refresh_token"), cuerpo.get("expires_in")
    if not isinstance(acceso, str) or not acceso or not isinstance(refresco, str) or not refresco:
        raise AuthNoDisponible()
    if not isinstance(expira, int) or isinstance(expira, bool) or expira <= 0:
        raise AuthNoDisponible()
    usuario = cuerpo.get("user")
    factores = usuario.get("factors") if isinstance(usuario, dict) else None
    verificados = tuple(
        str(f["id"])
        for f in (factores or [])
        if isinstance(f, dict)
        and f.get("factor_type") == "totp"
        and f.get("status") == "verified"
        and isinstance(f.get("id"), str)
    )
    return TokensSesion(acceso, refresco, expira, verificados)


class ClienteSupabaseAuth:
    """Cliente mínimo de GoTrue. `http` es opcional (inyectable en tests); sin él cada llamada abre uno propio."""

    def __init__(self, base_url: str, anon_key: str, *, http: httpx.AsyncClient | None = None, timeout: float = 10.0):
        if not base_url or not anon_key:
            raise ValueError("Se necesitan la URL de Supabase y la anon key")
        self._base = base_url.rstrip("/") + "/auth/v1"
        self._anon = anon_key
        self._http = http
        self._timeout = timeout

    @classmethod
    def desde_config(cls, config: Any, **kwargs: Any) -> "ClienteSupabaseAuth":
        return cls(config.supabase_url, config.supabase_anon_key, **kwargs)

    async def _llamar(
        self,
        metodo: str,
        ruta: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        access_token: str | None = None,
    ) -> httpx.Response:
        headers = {"apikey": self._anon, "Authorization": f"Bearer {access_token or self._anon}"}
        url = self._base + ruta
        try:
            if self._http is not None:
                r = await self._http.request(metodo, url, json=json, params=params, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    r = await http.request(metodo, url, json=json, params=params, headers=headers)
        except httpx.HTTPError:
            raise AuthNoDisponible() from None
        if r.status_code == 429:
            raise LimiteExcedido()
        if r.status_code >= 500:
            _log.warning("supabase_auth_5xx", extra={"estado": r.status_code, "ruta": ruta})
            raise AuthNoDisponible()
        return r

    async def login_password(self, email: str, password: str) -> TokensSesion:
        r = await self._llamar(
            "POST", "/token", json={"email": email, "password": password}, params={"grant_type": "password"}
        )
        if r.status_code in (400, 401, 422):
            raise CredencialesInvalidas()
        if r.status_code != 200:
            raise AuthNoDisponible()
        return _sesion(r)

    async def refrescar(self, refresh_token: str) -> TokensSesion:
        r = await self._llamar(
            "POST", "/token", json={"refresh_token": refresh_token}, params={"grant_type": "refresh_token"}
        )
        if r.status_code in _EXPIRADO:
            raise SesionExpirada()
        if r.status_code != 200:
            raise AuthNoDisponible()
        return _sesion(r)

    async def enrolar_totp(self, access_token: str, nombre_visible: str) -> EnrolamientoTOTP:
        # GoTrue exige un friendly_name único por usuario: se agrega un sufijo al azar.
        nombre = f"{nombre_visible}-{uuid.uuid4().hex[:6]}"
        r = await self._llamar(
            "POST",
            "/factors",
            json={"factor_type": "totp", "friendly_name": nombre, "issuer": nombre_visible},
            access_token=access_token,
        )
        if r.status_code in (401, 403):
            raise SesionExpirada()
        if r.status_code not in (200, 201):
            if 400 <= r.status_code < 500:
                raise SolicitudRechazada()
            raise AuthNoDisponible()
        cuerpo = _json(r)
        totp = cuerpo.get("totp")
        if (
            not isinstance(cuerpo.get("id"), str)
            or not isinstance(totp, dict)
            or not all(isinstance(totp.get(k), str) for k in ("qr_code", "secret", "uri"))
        ):
            raise AuthNoDisponible()
        return EnrolamientoTOTP(cuerpo["id"], totp["qr_code"], totp["secret"], totp["uri"])

    async def verificar_totp(self, access_token: str, factor_id: str, codigo: str) -> TokensSesion:
        factor = _uuid_texto(factor_id)
        desafio = await self._llamar("POST", f"/factors/{factor}/challenge", access_token=access_token)
        if desafio.status_code in (401, 403):
            raise SesionExpirada()
        if desafio.status_code != 200:
            raise SolicitudRechazada() if 400 <= desafio.status_code < 500 else AuthNoDisponible()
        challenge_id = _json(desafio).get("id")
        if not isinstance(challenge_id, str) or not challenge_id:
            raise AuthNoDisponible()
        r = await self._llamar(
            "POST",
            f"/factors/{factor}/verify",
            json={"challenge_id": challenge_id, "code": codigo},
            access_token=access_token,
        )
        if r.status_code in (400, 422):
            raise CodigoMFAInvalido()
        if r.status_code in (401, 403):
            raise SesionExpirada()
        if r.status_code != 200:
            raise AuthNoDisponible()
        return _sesion(r)

    async def aceptar_invitacion(self, token_hash: str, password: str) -> TokensSesion:
        """Canjea el `token_hash` del mail de invitación por una sesión y define la contraseña."""
        r = await self._llamar("POST", "/verify", json={"type": "invite", "token_hash": token_hash})
        if 400 <= r.status_code < 500:
            raise InvitacionInvalida()
        if r.status_code != 200:
            raise AuthNoDisponible()
        tokens = _sesion(r)
        r = await self._llamar("PUT", "/user", json={"password": password}, access_token=tokens.access_token)
        if r.status_code == 422 or (r.status_code == 400 and re.search("password", _codigo_error(r))):
            raise ContrasenaDebil()
        if r.status_code in (400, 401, 403):
            raise InvitacionInvalida()
        if r.status_code != 200:
            raise AuthNoDisponible()
        return tokens

    async def cerrar_sesion(self, access_token: str) -> None:
        """Invalida la sesión en Supabase (solo esta, `scope=local`). Es de mejor esfuerzo: nunca lanza."""
        try:
            await self._llamar("POST", "/logout", params={"scope": "local"}, access_token=access_token)
        except ErrorSupabaseAuth:
            _log.warning("supabase_logout_fallido")
