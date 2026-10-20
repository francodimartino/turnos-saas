"""Emisión y borrado de las cookies de sesión, y middleware de origen (CSRF) y de cookie a `Bearer`."""

import json
from http.cookies import CookieError, SimpleCookie
from typing import Literal, TypedDict, cast

from fastapi import Response
from turnos_supabase_proxy import TokensSesion

RUTA_REFRESCO = "/auth"
_METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})


class _OpcionesCookie(TypedDict):
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]


class CookiesSesion:
    """Las dos cookies de sesión de un backend: `<prefijo>_acceso` (todo el sitio) y `<prefijo>_refresco` (`/auth`)."""

    def __init__(self, prefijo: str, *, secure: bool, samesite: str, refresh_max_age: int):
        self.acceso = f"{prefijo}_acceso"
        self.refresco = f"{prefijo}_refresco"
        self._refresh_max_age = refresh_max_age
        # La config del backend ya validó `samesite` al cargarse (solo lax/strict); acá solo se le da el tipo.
        self._opciones: _OpcionesCookie = {
            "httponly": True,
            "secure": secure,
            "samesite": cast(Literal["lax", "strict", "none"], samesite),
        }

    def poner(self, respuesta: Response, tokens: TokensSesion) -> None:
        respuesta.set_cookie(self.acceso, tokens.access_token, max_age=tokens.expires_in, path="/", **self._opciones)
        respuesta.set_cookie(
            self.refresco,
            tokens.refresh_token,
            max_age=self._refresh_max_age,
            path=RUTA_REFRESCO,
            **self._opciones,
        )
        respuesta.headers["Cache-Control"] = "no-store"

    def borrar(self, respuesta: Response) -> None:
        respuesta.delete_cookie(self.acceso, path="/", **self._opciones)
        respuesta.delete_cookie(self.refresco, path=RUTA_REFRESCO, **self._opciones)
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

    def __init__(self, app, *, origen_permitido: str, cookie_acceso: str):
        self.app = app
        self._origen = origen_permitido.encode("latin-1")
        self._cookie_acceso = cookie_acceso

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
            acceso = _cookies(headers).get(self._cookie_acceso)
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
