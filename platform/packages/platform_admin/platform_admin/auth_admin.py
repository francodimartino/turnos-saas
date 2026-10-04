"""Cliente de la Auth Admin API de Supabase: el único lugar donde se usa `service_role`.

`service_role` (bypass total de RLS) solo existe para esta API, que no acepta otra credencial
(ARCHITECTURE.md §3.2). Se lee de la variable de entorno al construir el cliente y no sale de este módulo:
ni se pasa al pool de base, ni a los logs, ni a `repr`. Las operaciones de platform_admin dependen de la
interfaz `ClienteAuthAdmin`, que es inyectable (en los tests, un falso).
"""

import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from platform_admin.errores import ErrorInvitacion

_TIMEOUT_SEG = 10.0


class ClienteAuthAdmin(Protocol):
    """Lo mínimo que platform_admin necesita de la Auth Admin API."""

    async def invitar(self, email: str) -> uuid.UUID:
        """Invita a `email` y devuelve el id de su usuario de Auth. Lanza `ErrorInvitacion` si falla."""
        ...


class ClienteAuthAdminHttp:
    def __init__(self, url: str, service_role_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not url or not service_role_key:
            raise ValueError("Faltan la URL de Supabase o la credencial de la Auth Admin API")
        self._url = url.rstrip("/")
        self._clave = service_role_key
        self._transport = transport

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "ClienteAuthAdminHttp":
        url = entorno.get("SUPABASE_URL")
        clave = entorno.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not clave:
            raise ValueError("SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY son obligatorias para la Auth Admin API")
        return cls(url, clave)

    def __repr__(self) -> str:
        return f"ClienteAuthAdminHttp(url={self._url!r})"

    async def invitar(self, email: str) -> uuid.UUID:
        # Los mensajes de error nunca incluyen el email, el cuerpo de respuesta ni la credencial.
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT_SEG) as http:
                respuesta = await http.post(
                    f"{self._url}/auth/v1/invite",
                    headers={"apikey": self._clave, "Authorization": f"Bearer {self._clave}"},
                    json={"email": email},
                )
        except httpx.HTTPError as exc:
            raise ErrorInvitacion(f"Auth Admin API inalcanzable ({type(exc).__name__})") from None
        if respuesta.status_code >= 300:
            raise ErrorInvitacion(f"Auth Admin API respondió {respuesta.status_code}")
        try:
            cuerpo: Any = respuesta.json()
            return uuid.UUID(str(cuerpo["id"]))
        except (ValueError, KeyError, TypeError):
            raise ErrorInvitacion("Auth Admin API devolvió una respuesta sin id de usuario") from None
