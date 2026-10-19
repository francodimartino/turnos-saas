"""Cliente de la Auth Admin API de Supabase: el único lugar del código que habla con `/auth/v1/invite` y
`/auth/v1/admin/*` y que usa la `service_role`.

La `service_role` (bypass total de RLS) solo existe para esta API, que no acepta otra credencial (ARCHITECTURE.md
§3.2). `SupabaseAuthAdmin` cubre las dos operaciones que el producto necesita: invitar (al staff de un tenant desde el
dashboard y al owner en el alta de tenants del admin) y eliminar la cuenta de un usuario (purga de tenants). Cada
servicio lo crea UNA vez al arrancar, con su propia clave, y lo recibe inyectado; las capas de dominio dependen de las
interfaces (`ClienteAuthAdmin` en `platform_auth.invitaciones` y en `platform_admin.auth_admin`,
`ClienteAuthEliminacion` en `platform_admin.auth_admin`), no de esta clase: en los tests se reemplaza por un falso.

La clave no se pasa al pool de base, ni a los logs, ni a `repr`, y ningún mensaje de error incluye el email, el cuerpo
de la respuesta ni la clave.
"""

import os
import uuid
from collections.abc import Mapping
from typing import Any

import httpx

_TIMEOUT_SEG = 10.0


class ErrorInvitacion(Exception):
    """Falló la llamada a la Auth Admin API. `reintentable` dice si repetirla puede funcionar.

    El mensaje nunca incluye el email ni la clave de servicio ni el cuerpo de la respuesta.
    """

    def __init__(self, mensaje: str, *, reintentable: bool = False):
        super().__init__(mensaje)
        self.reintentable = reintentable


class ErrorEliminacionCuenta(Exception):
    """Falló la eliminación de una cuenta por la Auth Admin API (red o respuesta que no sea 2xx/404). Sin PII."""


class SupabaseAuthAdmin:
    """Cliente de la Auth Admin API de Supabase con la `service_role` key: invitar y eliminar usuarios.

    La clave se pasa al construir (en producción, `desde_entorno()` al arrancar): nunca va en el repo ni en logs ni en
    errores.
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = _TIMEOUT_SEG,
        redirect_to: str | None = None,
    ):
        if not base_url or not service_role_key:
            raise ValueError("base_url y service_role_key son obligatorias")
        self.base_url = base_url.rstrip("/")
        self._clave = service_role_key
        self._http = http
        self._timeout = timeout
        self._redirect_to = redirect_to

    def __repr__(self) -> str:
        return f"SupabaseAuthAdmin(base_url={self.base_url!r})"

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None, **kwargs: Any) -> "SupabaseAuthAdmin":
        """Lee `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` de `entorno` (por defecto, `os.environ`).

        Lanza `ValueError` con un mensaje claro si falta alguna: los servicios que usan la API la llaman al arrancar.
        """
        origen = os.environ if entorno is None else entorno
        url = origen.get("SUPABASE_URL")
        clave = origen.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not clave:
            raise ValueError("faltan SUPABASE_URL y/o SUPABASE_SERVICE_ROLE_KEY en el entorno")
        return cls(url, clave, **kwargs)

    async def _enviar(self, metodo: str, ruta: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{ruta}"
        headers = {"apikey": self._clave, "Authorization": f"Bearer {self._clave}"}
        if self._http is not None:
            return await self._http.request(metodo, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            return await http.request(metodo, url, headers=headers, **kwargs)

    async def invitar_por_email(self, email: str, nombre: str | None = None) -> uuid.UUID:
        """Crea (o reutiliza, si aún no confirmó) la cuenta, envía la invitación y devuelve el id del usuario.

        `nombre` viaja como user metadata `full_name` (lo lee `public.get_tenant_staff_identity`).
        """
        cuerpo: dict[str, Any] = {"email": email}
        if nombre is not None:
            cuerpo["data"] = {"full_name": nombre}
        params = {"redirect_to": self._redirect_to} if self._redirect_to else None
        try:
            r = await self._enviar("POST", "/auth/v1/invite", json=cuerpo, params=params)
        except httpx.HTTPError:
            raise ErrorInvitacion("No se pudo contactar a la Auth Admin API", reintentable=True) from None
        if r.status_code == 429 or r.status_code >= 500:
            raise ErrorInvitacion(f"La Auth Admin API respondió {r.status_code}", reintentable=True)
        if r.status_code != 200:
            raise ErrorInvitacion(f"La Auth Admin API rechazó la invitación ({r.status_code})", reintentable=False)
        try:
            return uuid.UUID(str(r.json()["id"]))
        except (ValueError, KeyError, TypeError):
            raise ErrorInvitacion("Respuesta inesperada de la Auth Admin API", reintentable=False) from None

    async def invitar(self, email: str, nombre: str | None = None) -> uuid.UUID:
        """Igual que `invitar_por_email`: es el nombre que usa la interfaz de `platform_admin` (alta del owner)."""
        return await self.invitar_por_email(email, nombre)

    async def eliminar_usuario(self, auth_user_id: uuid.UUID) -> bool:
        """Elimina la cuenta. `True` si existía y se borró; `False` si ya no existía (404: cuenta como éxito).

        Lanza `ErrorEliminacionCuenta` ante cualquier otro fallo.
        """
        # Solo un UUID real entra al path: nada del input puede reescribir la ruta de la API.
        if not isinstance(auth_user_id, uuid.UUID):
            raise ValueError("auth_user_id debe ser un UUID")
        try:
            r = await self._enviar("DELETE", f"/auth/v1/admin/users/{auth_user_id}")
        except httpx.HTTPError as exc:
            raise ErrorEliminacionCuenta(f"Auth Admin API inalcanzable ({type(exc).__name__})") from None
        if r.status_code == 404:
            return False
        if r.status_code >= 300:
            raise ErrorEliminacionCuenta(f"Auth Admin API respondió {r.status_code}")
        return True
