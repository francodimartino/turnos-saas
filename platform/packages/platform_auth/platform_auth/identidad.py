"""Identidad extraída de un JWT ya verificado."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentidadJWT:
    """Lo que dice el token (verificado en firma). Son claims: el estado vigente se lee en vivo de la base.

    `tenant_id` es None para Franco o para staff desactivado (Auth Hook, §3.4); `aal` es None si el token no lo trae.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    is_owner: bool
    is_platform_admin: bool
    aal: str | None
