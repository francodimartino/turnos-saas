"""Cliente de la Auth Admin API de Supabase para platform_admin.

`service_role` (bypass total de RLS) solo existe para la Auth Admin API, que no acepta otra credencial
(ARCHITECTURE.md §3.2). Hoy hay DOS clientes de esa API, ambos con `service_role`:
  - `ClienteAuthAdminHttp` (este módulo): invita a un owner al dar de alta un tenant (`invitar`) y elimina la
    cuenta de Auth en la purga de tenants (`eliminar_usuario`);
  - `SupabaseAuthAdmin` (`platform_auth/invitaciones.py`): invita al staff de un tenant desde el dashboard.
Unificarlos en uno solo es una deuda conocida, todavía no hecha.
La clave se lee de la variable de entorno al construir el cliente y no se pasa al pool de base, ni a los logs, ni a
`repr`. Las operaciones de platform_admin dependen de las interfaces `ClienteAuthAdmin` y `ClienteAuthEliminacion`,
que son inyectables (en los tests, un falso).
"""

import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from platform_admin.errores import ErrorEliminacionCuenta, ErrorInvitacion

_TIMEOUT_SEG = 10.0


class ClienteAuthAdmin(Protocol):
    """Lo mínimo que platform_admin necesita de la Auth Admin API."""

    async def invitar(self, email: str, nombre: str | None = None) -> uuid.UUID:
        """Invita a `email` y devuelve el id de su usuario de Auth. Lanza `ErrorInvitacion` si falla.

        `nombre` viaja como user metadata `full_name` (lo lee `public.get_tenant_staff_identity`).
        """
        ...


class ClienteAuthEliminacion(Protocol):
    """Lo mínimo que la purga de tenants necesita de la Auth Admin API (§2.2 paso 2)."""

    async def eliminar_usuario(self, auth_user_id: uuid.UUID) -> bool:
        """Elimina la cuenta. `True` si existía y se borró; `False` si ya no existía (404: cuenta como éxito).

        Lanza `ErrorEliminacionCuenta` ante cualquier otro fallo.
        """
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

    async def invitar(self, email: str, nombre: str | None = None) -> uuid.UUID:
        cuerpo_invitacion: dict[str, Any] = {"email": email}
        if nombre is not None:
            cuerpo_invitacion["data"] = {"full_name": nombre}
        # Los mensajes de error nunca incluyen el email, el cuerpo de respuesta ni la credencial.
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT_SEG) as http:
                respuesta = await http.post(
                    f"{self._url}/auth/v1/invite",
                    headers={"apikey": self._clave, "Authorization": f"Bearer {self._clave}"},
                    json=cuerpo_invitacion,
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

    async def eliminar_usuario(self, auth_user_id: uuid.UUID) -> bool:
        # Solo un UUID real entra al path: nada del input puede reescribir la ruta de la API.
        if not isinstance(auth_user_id, uuid.UUID):
            raise ValueError("auth_user_id debe ser un UUID")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT_SEG) as http:
                respuesta = await http.delete(
                    f"{self._url}/auth/v1/admin/users/{auth_user_id}",
                    headers={"apikey": self._clave, "Authorization": f"Bearer {self._clave}"},
                )
        except httpx.HTTPError as exc:
            raise ErrorEliminacionCuenta(f"Auth Admin API inalcanzable ({type(exc).__name__})") from None
        if respuesta.status_code == 404:
            return False
        if respuesta.status_code >= 300:
            raise ErrorEliminacionCuenta(f"Auth Admin API respondió {respuesta.status_code}")
        return True
