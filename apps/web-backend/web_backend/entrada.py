"""Tope del tamaño de la URL de cada petición (ARCHITECTURE.md §3.7).

ASGI puro. El cuerpo ya lo acota `turnos_seguridad_http.MiddlewareLimiteBody`; acá se acota lo otro que manda el
cliente sin cuerpo: la ruta y el query. Una URL más larga que `MAX_BYTES_URL` no llega a ninguna ruta: 414 con el
mismo formato de error que el resto, sin repetir lo recibido.
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
