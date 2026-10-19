"""Cookies de sesión hacia el navegador y middleware que las convierte en `Authorization: Bearer`.

Mismo mecanismo que el dashboard-backend (ver su README), con cookies propias (`adm_*`) para no mezclar sesiones:
  - Los tokens viajan **solo** en cookies `HttpOnly` + `Secure` + `SameSite=Strict` (configurable a `Lax`; `None`
    no se admite). Nunca en el body de una respuesta.
  - `adm_acceso` vale en todo el sitio; `adm_refresco` solo se envía a `/auth/*`.
  - CSRF en capas: `SameSite`, CORS acotado al origen del admin-frontend y este middleware, que exige `Origin`
    igual al del admin-frontend en todo método que escribe, salvo con `Authorization` explícito.
  - Sin `Authorization` pero con la cookie de acceso, la inyecta como `Bearer` para que `platform_auth` siga siendo
    la única puerta.
"""

import json
from http.cookies import CookieError, SimpleCookie
from typing import Literal, TypedDict, cast

from fastapi import Response
from supabase_proxy import TokensSesion

from admin_backend.config import Config

COOKIE_ACCESO = "adm_acceso"
COOKIE_REFRESCO = "adm_refresco"
RUTA_REFRESCO = "/auth"
_METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})


class _OpcionesCookie(TypedDict):
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]


def _opciones(config: Config) -> _OpcionesCookie:
    # `Config` ya validó `cookie_samesite` al cargarse (solo lax/strict); acá solo se le da el tipo.
    samesite = cast(Literal["lax", "strict", "none"], config.cookie_samesite)
    return {"httponly": True, "secure": config.cookie_secure, "samesite": samesite}


def poner_sesion(respuesta: Response, tokens: TokensSesion, config: Config) -> None:
    opciones = _opciones(config)
    respuesta.set_cookie(COOKIE_ACCESO, tokens.access_token, max_age=tokens.expires_in, path="/", **opciones)
    respuesta.set_cookie(
        COOKIE_REFRESCO, tokens.refresh_token, max_age=config.cookie_refresh_max_age, path=RUTA_REFRESCO, **opciones
    )
    respuesta.headers["Cache-Control"] = "no-store"


def borrar_sesion(respuesta: Response, config: Config) -> None:
    opciones = _opciones(config)
    respuesta.delete_cookie(COOKIE_ACCESO, path="/", **opciones)
    respuesta.delete_cookie(COOKIE_REFRESCO, path=RUTA_REFRESCO, **opciones)
    respuesta.headers["Cache-Control"] = "no-store"


def _cookies(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    for nombre, valor in headers:
        if nombre == b"cookie":
            try:
                jar.load(valor.decode("latin-1"))
            except CookieError:
                continue
    return {k: m.value for k, m in jar.items()}


class MiddlewareSesionCookie:
    """ASGI puro: comprobación de origen (CSRF) e inyección del access token de la cookie como Bearer."""

    def __init__(self, app, *, origen_permitido: str):
        self.app = app
        self._origen = origen_permitido.encode("latin-1")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: list[tuple[bytes, bytes]] = list(scope["headers"])
        nombres = {n for n, _ in headers}
        con_auth_explicita = b"authorization" in nombres

        if scope["method"] not in _METODOS_SEGUROS and not con_auth_explicita:
            origen = next((v for n, v in headers if n == b"origin"), None)
            if origen != self._origen:
                await _rechazar_origen(send)
                return

        if not con_auth_explicita:
            acceso = _cookies(headers).get(COOKIE_ACCESO)
            if acceso:
                try:
                    valor = b"Bearer " + acceso.encode("latin-1")
                except UnicodeEncodeError:
                    valor = None
                if valor is not None:
                    scope = {**scope, "headers": [*headers, (b"authorization", valor)]}
        await self.app(scope, receive, send)


async def _rechazar_origen(send) -> None:
    cuerpo = json.dumps({"detail": {"codigo": "origen_no_permitido", "mensaje": "Origen no permitido"}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(cuerpo)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})
