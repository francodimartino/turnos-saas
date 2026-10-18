"""Claves públicas (JWKS) de Supabase Auth en memoria, descargadas SIN bloquear el event loop.

Reemplaza a `jwt.PyJWKClient`, que es sincrónico: un `kid` desconocido forzaba una descarga HTTP bloqueante dentro del
request (y un atacante con `kid` aleatorios podía congelar el loop). Acá:

  - Las claves se descargan al arrancar (`abrir`, desde el lifespan) con un cliente HTTP asíncrono y se refrescan en una
    tarea de fondo (`intervalo_seg`); `cerrar` la cancela al apagar.
  - `get_signing_key_from_jwt` es sincrónico y solo mira la memoria: nunca hace red. Un `kid` desconocido da 401 y, como
    mucho una vez cada `min_entre_refrescos_kid_seg`, despierta a la tarea de fondo para que lo busque (rotación de
    claves): el refresco por `kid` desconocido tiene límite de frecuencia, no se puede forzar con tokens falsos.
  - Si la descarga falla (al arrancar o después) se conserva el último JWKS válido y se registra un warning sin datos
    sensibles. Si todavía no hay ninguna clave (arrancó con el JWKS inalcanzable) la app arranca igual, reintenta cada
    `reintento_seg` en segundo plano y los requests con token reciben 503 `auth_no_disponible` hasta que haya claves.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

from platform_auth.errores import ClavesNoDisponibles

_log = logging.getLogger("platform_auth.jwks")

INTERVALO_SEG = 600.0
REINTENTO_SIN_CLAVES_SEG = 15.0
MIN_ENTRE_REFRESCOS_KID_SEG = 60.0
TIMEOUT_DESCARGA_SEG = 5.0
_MAX_BYTES_JWKS = 1_000_000


class ProveedorJWKS:
    def __init__(
        self,
        url: str,
        *,
        intervalo_seg: float = INTERVALO_SEG,
        reintento_seg: float = REINTENTO_SIN_CLAVES_SEG,
        min_entre_refrescos_kid_seg: float = MIN_ENTRE_REFRESCOS_KID_SEG,
        timeout_seg: float = TIMEOUT_DESCARGA_SEG,
        transporte: httpx.AsyncBaseTransport | None = None,
        reloj: Callable[[], float] = time.monotonic,
    ):
        self._url = url
        self._intervalo = intervalo_seg
        self._reintento = reintento_seg
        self._min_kid = min_entre_refrescos_kid_seg
        self._timeout = timeout_seg
        self._transporte = transporte
        self._reloj = reloj
        self._claves: dict[str, Any] = {}
        self._cliente: httpx.AsyncClient | None = None
        self._tarea: asyncio.Task | None = None
        self._evento: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ultimo_intento: float | None = None

    @property
    def tiene_claves(self) -> bool:
        return bool(self._claves)

    # --- ciclo de vida (lifespan) ---
    async def abrir(self) -> None:
        """Descarga el JWKS inicial (sin lanzar si falla) y arranca el refresco en segundo plano. Idempotente."""
        if self._tarea is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._evento = asyncio.Event()
        self._cliente = httpx.AsyncClient(timeout=self._timeout, transport=self._transporte, follow_redirects=False)
        await self._refrescar_seguro()
        self._tarea = asyncio.create_task(self._bucle(), name="refresco-jwks")

    async def cerrar(self) -> None:
        tarea, self._tarea = self._tarea, None
        if tarea is not None:
            tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarea
        cliente, self._cliente = self._cliente, None
        if cliente is not None:
            await cliente.aclose()
        self._loop = None
        self._evento = None

    # --- descarga ---
    async def refrescar(self) -> None:
        """Descarga y reemplaza el JWKS. Lanza si la descarga o el contenido no sirven (se conserva el anterior)."""
        cliente = self._cliente
        if cliente is None:
            raise RuntimeError("ProveedorJWKS no está abierto")
        respuesta = await cliente.get(self._url)
        respuesta.raise_for_status()
        if len(respuesta.content) > _MAX_BYTES_JWKS:
            raise ValueError("JWKS demasiado grande")
        nuevas: dict[str, Any] = {}
        for item in respuesta.json().get("keys", []):
            kid = item.get("kid") if isinstance(item, dict) else None
            if not isinstance(kid, str) or not kid:
                continue
            if item.get("kty") == "oct":
                continue  # un JWKS público no debe traer secretos simétricos
            try:
                nuevas[kid] = jwt.PyJWK(item)
            except Exception:  # noqa: BLE001 - PyJWK lanza PyJWTError, KeyError o ValueError según el defecto
                continue  # clave de un tipo/algoritmo no soportado: se ignora, no tumba al resto
        if not nuevas:
            raise ValueError("JWKS sin claves utilizables")
        self._claves = nuevas

    async def _refrescar_seguro(self) -> None:
        self._ultimo_intento = self._reloj()
        try:
            await self.refrescar()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - cualquier falla conserva el último JWKS válido
            _log.warning(
                "jwks_refresco_fallido: %s (%s)",
                type(error).__name__,
                "se conserva el último JWKS" if self._claves else "todavía sin claves",
            )

    async def _bucle(self) -> None:
        evento = self._evento
        if evento is None:
            return
        while True:
            espera = self._intervalo if self._claves else self._reintento
            vence = (self._ultimo_intento or 0.0) + espera
            restante = max(0.0, vence - self._reloj())
            try:
                await asyncio.wait_for(evento.wait(), timeout=restante)
            except asyncio.TimeoutError:  # noqa: UP041 - en Python < 3.11 no es el TimeoutError builtin
                await self._refrescar_seguro()
                continue
            evento.clear()
            # Despertó un `kid` desconocido: solo se busca si pasó el mínimo desde el último intento.
            if self._ultimo_intento is None or self._reloj() - self._ultimo_intento >= self._min_kid:
                await self._refrescar_seguro()

    # --- consulta (sincrónica, solo memoria) ---
    def get_signing_key_from_jwt(self, token: str) -> Any:
        """Clave de firma del `kid` del token. Sin red: `kid` desconocido => 401 (PyJWTError) y pide un refresco."""
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str) or not kid:
            raise jwt.PyJWKClientError("El token no trae kid")
        if not self._claves:
            raise ClavesNoDisponibles()
        clave = self._claves.get(kid)
        if clave is None:
            self._pedir_refresco()
            raise jwt.PyJWKClientError("kid desconocido")
        return clave

    def _pedir_refresco(self) -> None:
        loop, evento = self._loop, self._evento
        if loop is None or evento is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(evento.set)
