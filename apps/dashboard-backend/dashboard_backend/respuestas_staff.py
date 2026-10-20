"""Modelos de respuesta del staff del local: lo que sale por HTTP. Nunca traen el `auth_user_id`."""

from pydantic import BaseModel


class StaffItemOut(BaseModel):
    id: str
    nombre: str | None
    email: str | None
    is_owner: bool
    is_active: bool
    es_yo: bool
    created_at: str
    permission_keys: list[str]


class ListaStaffOut(BaseModel):
    staff: list[StaffItemOut]


class InvitacionOut(BaseModel):
    """201 si se creó el alta, 200 si la persona ya estaba en el equipo."""

    tenant_user_id: str
    creado: bool


class PermisosStaffOut(BaseModel):
    id: str
    permission_keys: list[str]


class EstadoStaffOut(BaseModel):
    id: str
    is_owner: bool
    is_active: bool
