"""Middlewares ASGI puros (sin `BaseHTTPMiddleware`: no bufferizan ni leen el body).

Headers de seguridad: se agregan a toda respuesta HTTP, incluidas las de error (404, 422, 429, 503, 413).
  - `Strict-Transport-Security`: 2 años, `includeSubDomains`.
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
  - `Cache-Control: no-store` solo si la ruta no fijó uno propio (las cookies de sesión ya lo fijan igual).
Los demás headers que la ruta o el CORS hayan puesto no se tocan, salvo `Server`, que se quita siempre.

Excepciones no manejadas: Starlette las convierte en 500 en su capa más externa, por fuera de este middleware, y
esa respuesta saldría sin headers. Por eso `MiddlewareHeadersSeguridad` las atrapa (si la respuesta aún no empezó),
las reporta a Sentry (pasa por `before_send`, el filtro de PII) y responde un 500 genérico con los headers.

Límite de body: `HTTP_MAX_BODY_BYTES` (por defecto 1 MiB). Los payloads reales de estos servicios son JSON de unos
pocos KB (un update de Telegram pesa del orden de 1-5 KB; un login, menos de 1 KB), así que 1 MiB deja un margen
enorme sin permitir cuerpos que agoten la memoria. Se rechaza con 413:
  - por `Content-Length` declarado mayor al límite, sin leer nada del cuerpo;
  - en streaming (sin `Content-Length`, p. ej. chunked) en cuanto los bytes recibidos superan el límite.
"""

import json
import os

import sentry_sdk

VARIABLE_LIMITE_BODY = "HTTP_MAX_BODY_BYTES"
LIMITE_BODY_POR_DEFECTO = 1024 * 1024

_HEADERS_FIJOS: tuple[tuple[bytes, bytes], ...] = (
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


def limite_body_desde_entorno() -> int:
    """Lee `HTTP_MAX_BODY_BYTES`; valor ausente o inválido (no entero o <= 0) cae al defecto."""
    crudo = os.environ.get(VARIABLE_LIMITE_BODY, "").strip()
    try:
        valor = int(crudo)
    except ValueError:
        return LIMITE_BODY_POR_DEFECTO
    return valor if valor > 0 else LIMITE_BODY_POR_DEFECTO


_CUERPO_500 = json.dumps(
    {"detail": {"codigo": "error_interno", "mensaje": "Error interno del servidor."}}
).encode()


class MiddlewareHeadersSeguridad:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        empezo = False

        async def _send(mensaje):
            nonlocal empezo
            if mensaje["type"] == "http.response.start":
                empezo = True
                headers = [h for h in mensaje.get("headers", []) if h[0].lower() != b"server"]
                presentes = {nombre.lower() for nombre, _ in headers}
                for nombre, valor in _HEADERS_FIJOS:
                    if nombre not in presentes:
                        headers.append((nombre, valor))
                if b"cache-control" not in presentes:
                    headers.append((b"cache-control", b"no-store"))
                mensaje = {**mensaje, "headers": headers}
            await send(mensaje)

        try:
            await self.app(scope, receive, _send)
        except Exception as error:
            if empezo:
                raise  # ya salió parte de la respuesta: no se puede cambiar el status.
            sentry_sdk.capture_exception(error)
            await _send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_CUERPO_500)).encode()),
                    ],
                }
            )
            await _send({"type": "http.response.body", "body": _CUERPO_500})


class _CuerpoExcedido(Exception):
    pass


_CUERPO_413 = json.dumps(
    {"detail": {"codigo": "cuerpo_demasiado_grande", "mensaje": "El cuerpo de la petición supera el límite."}}
).encode()


async def _responder_413(send) -> None:
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


class MiddlewareLimiteBody:
    def __init__(self, app, limite_bytes: int | None = None):
        self.app = app
        self.limite = limite_bytes if limite_bytes is not None else limite_body_desde_entorno()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declarado = dict(scope["headers"]).get(b"content-length")
        if declarado is not None:
            try:
                if int(declarado) > self.limite:
                    await _responder_413(send)
                    return
            except ValueError:
                pass  # Content-Length inválido: lo rechaza el servidor; igual se cuenta lo que llegue.

        recibidos = 0
        excedido = False
        respondido = False

        async def _receive():
            nonlocal recibidos, excedido
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibidos += len(mensaje.get("body", b""))
                if recibidos > self.limite:
                    excedido = True
                    raise _CuerpoExcedido
            return mensaje

        async def _send(mensaje):
            # Si se excedió, lo que la app haya traducido de la excepción (un 400 de FastAPI, p. ej.) se
            # reemplaza por la 413 y se descarta el resto de su respuesta.
            nonlocal respondido
            if not excedido:
                await send(mensaje)
                return
            if not respondido:
                respondido = True
                await _responder_413(send)

        try:
            await self.app(scope, _receive, _send)
        except _CuerpoExcedido:
            pass
        if excedido and not respondido:
            await _responder_413(send)
