"""Verificación de Cloudflare Turnstile (captcha) contra `siteverify`.

- Sin `TURNSTILE_SECRET_KEY` (vacía o ausente) el verificador está apagado: `verificar` devuelve siempre True y
  no hace llamadas de red (desarrollo local, entornos sin Turnstile).
- Con clave: token ausente o vacío -> False sin llamar a la red; token inválido -> False; error de red, timeout,
  respuesta 5xx o respuesta ilegible -> `TurnstileNoDisponible` (falla cerrado: el llamador debe rechazar el
  login, y puede distinguirlo de un token inválido para responder 503 en vez de 400).

Nunca se loguea el token, la IP ni el secreto.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SEG = 5.0


class TurnstileNoDisponible(Exception):
    """No se pudo consultar a Cloudflare (red, timeout, 5xx o respuesta ilegible)."""


class VerificadorTurnstile:
    def __init__(self, secret: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secret = (secret if secret is not None else os.environ.get("TURNSTILE_SECRET_KEY", "")).strip()
        self._transport = transport

    @property
    def activo(self) -> bool:
        return bool(self._secret)

    async def verificar(self, token: str | None, ip: str | None = None) -> bool:
        if not self.activo:
            return True
        if not token:
            return False
        datos = {"secret": self._secret, "response": token}
        if ip:
            datos["remoteip"] = ip
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SEG) as cliente:
                resp = await cliente.post(SITEVERIFY_URL, data=datos)
        except httpx.HTTPError as exc:
            logger.warning("turnstile_error_de_red tipo=%s", type(exc).__name__)
            raise TurnstileNoDisponible("error de red consultando Turnstile") from None
        if resp.status_code >= 500:
            logger.warning("turnstile_error_servidor status=%s", resp.status_code)
            raise TurnstileNoDisponible("Turnstile respondió con error de servidor")
        try:
            cuerpo = resp.json()
        except ValueError:
            logger.warning("turnstile_respuesta_ilegible status=%s", resp.status_code)
            raise TurnstileNoDisponible("respuesta ilegible de Turnstile") from None
        exito = isinstance(cuerpo, dict) and cuerpo.get("success") is True
        if not exito:
            logger.info("turnstile_token_rechazado")
        return exito
