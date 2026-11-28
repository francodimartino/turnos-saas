"""Topes de la petición que no cubre `MiddlewareLimiteBody` (ARCHITECTURE.md §3.7).

ASGI puro. `turnos_seguridad_http.MiddlewareLimiteBody` acota el cuerpo, pero solo cuando la ruta lo lee (por ejemplo
para validar un JSON): una ruta de lectura (`GET`, `HEAD`) no lo lee nunca, así que un cuerpo en streaming sin
`Content-Length` no pasaría por ese límite. Por eso `MiddlewareSinCuerpoEnLecturas` rechaza con 413 todo cuerpo con
`Transfer-Encoding` en esos métodos; el que declara `Content-Length` ya lo corta el límite de body.

`MiddlewareLargoUrl` acota lo otro que manda el cliente sin cuerpo: la ruta y el query. Una URL más larga que
`MAX_BYTES_URL` no llega a ninguna ruta: 414 con el mismo formato de error que el resto, sin repetir lo recibido.
"""

import json

MAX_BYTES_URL = 2048

_CUERPO_414 = json.dumps(
    {"detail": {"codigo": "url_demasiado_larga", "mensaje": "La dirección del pedido supera el largo permitido."}}
).encode()


class MiddlewareLargoUrl:
    def __init__(self, app, max_bytes: int = MAX_BYTES_URL):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            largo = len(scope.get("raw_path") or scope["path"].encode()) + len(scope.get("query_string", b""))
            if largo > self.max_bytes:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 414,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(_CUERPO_414)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": _CUERPO_414})
                return
        await self.app(scope, receive, send)


_CUERPO_413 = json.dumps(
    {"detail": {"codigo": "cuerpo_demasiado_grande", "mensaje": "Este recurso no admite un cuerpo en la petición."}}
).encode()
METODOS_DE_LECTURA = frozenset({"GET", "HEAD"})


class MiddlewareSinCuerpoEnLecturas:
    """Rechaza (413) un cuerpo con `Transfer-Encoding` en `GET` y `HEAD`: esas rutas no leen el cuerpo."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["method"] in METODOS_DE_LECTURA
            and any(nombre.lower() == b"transfer-encoding" for nombre, _ in scope["headers"])
        ):
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_CUERPO_413)).encode()),
                        (b"connection", b"close"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _CUERPO_413})
            return
        await self.app(scope, receive, send)
