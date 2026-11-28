"""Modelos de respuesta de la configuración de avisos al negocio. Nunca traen el `auth_user_id`."""

from pydantic import BaseModel


class DestinatarioOut(BaseModel):
    id: str
    nombre: str | None
    email: str | None
    is_owner: bool
    # null en quien aún no aceptó la invitación: no tiene cuenta con la que compararlo.
    es_yo: bool | None
    # true si no tiene fila propia y rige el default por rol (owner y quienes pueden aprobar turnos)
    es_default: bool
    recibe_avisos: bool
    canal_email: bool
    canal_whatsapp: bool
    # lo que rige para el envío por email: recibe_avisos y canal_email
    recibe_por_email: bool


class CanalOut(BaseModel):
    canal: str
    disponible: bool


class AvisosOut(BaseModel):
    staff: list[DestinatarioOut]
    canales: list[CanalOut]
