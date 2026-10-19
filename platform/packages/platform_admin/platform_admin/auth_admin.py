"""Interfaces de la Auth Admin API que necesita platform_admin.

La implementación sobre HTTP es `platform_auth.SupabaseAuthAdmin`, el único cliente de esa API (con `service_role`):
cada servicio la crea una vez al arrancar y la inyecta. Las operaciones de platform_admin dependen de estas dos
interfaces, que en los tests se reemplazan por un falso.
"""

import uuid
from typing import Protocol


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
