"""Cliente de Resend por HTTP (httpx). Se crea una vez al arrancar y se reutiliza.

Reintenta solo ante 429 y 5xx. Un fallo de red o un timeout NO se reintenta: el email pudo haber sido aceptado y,
sin clave de idempotencia, reintentar lo duplicaría; el outbox decide más arriba qué hacer con ese `ErrorEmail`.
`timeout_total` es un plazo real de todo `enviar`, esperas de reintento incluidas (`asyncio.wait_for`). Si la espera
de un reintento (`Retry-After` o backoff) no entra en el plazo que queda, no se espera: se lanza enseguida el último
`ErrorEmail` real (429 o 5xx, reintentable). `tope_espera` por defecto es menor que `timeout_total` para que al
menos una espera entre en el plazo.

No loguea ni propaga el destino, el asunto, el cuerpo ni la API key; el cuerpo de error de Resend tampoco se refleja.
No lee el entorno: recibe una `ConfigEmail` ya validada.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from .config import ConfigEmail
from .errores import ErrorEmail

_log = logging.getLogger(__name__)

Dormir = Callable[[float], Awaitable[None]]


class ClienteEmail:
    def __init__(
        self,
        config: ConfigEmail,
        *,
        transporte: httpx.AsyncBaseTransport | None = None,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        self._config = config
        self._dormir = dormir
        self._http = httpx.AsyncClient(
            base_url=config.url_base,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=httpx.Timeout(config.timeout_total, connect=config.timeout_conexion),
            transport=transporte,
        )

    async def cerrar(self) -> None:
        await self._http.aclose()

    async def enviar(self, destino: str, asunto: str, texto: str, html: str | None = None) -> None:
        """Envía un email. Lanza `ErrorEmail` si falla; reintenta solo ante 429 y 5xx, dentro del plazo total."""
        cuerpo: dict[str, object] = {
            "from": self._config.remitente,
            "to": [destino],
            "subject": asunto,
            "text": texto,
        }
        if html is not None:
            cuerpo["html"] = html
        limite = time.monotonic() + self._config.timeout_total
        try:
            await asyncio.wait_for(self._con_reintentos(cuerpo, limite), self._config.timeout_total)
        except asyncio.TimeoutError:  # noqa: UP041 - en 3.10 no es el mismo tipo que TimeoutError
            _log.warning("Falló el envío de email", extra={"motivo": "plazo_total", "reintentable": False})
            raise ErrorEmail() from None

    async def _con_reintentos(self, cuerpo: dict[str, object], limite: float) -> None:
        intento = 0
        while True:
            error = await self._intentar(cuerpo)
            if error is None:
                return
            if not error.reintentable or intento >= self._config.max_reintentos:
                _log.warning(
                    "Falló el envío de email",
                    extra={"estado": error.estado_http, "intento": intento + 1, "reintentable": error.reintentable},
                )
                raise error
            intento += 1
            espera = error.retry_after if error.retry_after is not None else min(2.0**intento, self._config.tope_espera)
            if espera >= limite - time.monotonic():
                _log.warning(
                    "Falló el envío de email",
                    extra={"estado": error.estado_http, "intento": intento, "reintentable": True},
                )
                raise error
            await self._dormir(espera)

    async def _intentar(self, cuerpo: dict[str, object]) -> ErrorEmail | None:
        try:
            respuesta = await self._http.post("/emails", json=cuerpo)
        except httpx.HTTPError:
            # El mensaje de httpx puede traer datos del pedido: solo se conserva que no hubo respuesta.
            return ErrorEmail()
        if respuesta.is_success:
            return None
        return ErrorEmail(estado_http=respuesta.status_code, retry_after=self._retry_after(respuesta))

    def _retry_after(self, respuesta: httpx.Response) -> float | None:
        crudo = respuesta.headers.get("retry-after")
        if crudo is None:
            return None
        try:
            segundos = float(crudo)
        except ValueError:
            return None
        return max(0.0, min(segundos, self._config.tope_espera))
