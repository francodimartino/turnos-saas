"""Cambio del slug de un tenant desde el panel admin (ARCHITECTURE.md §2.12).

El cambio lo hace `public.cambiar_slug_tenant` (solo `platform_admin`): valida de nuevo, guarda el slug viejo 180 días
en `slugs_anteriores` para redirigir el link y deja `audit_log` con el actor de la transacción (`admin_tx`). Acá se
valida antes (formato, largo, reservados, estado del tenant) para responder con un error de dominio claro sin llegar a
la base, y los rechazos que la base igual hace por una carrera se traducen por SQLSTATE, nunca por el texto del mensaje.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from platform_tenancy import admin_tx

from platform_admin.errores import (
    CambioSlugConcurrente,
    ErrorDominioAdmin,
    PurgaEnCurso,
    SlugEnUso,
    SlugInvalido,
    SlugReservado,
    SlugSinCambio,
    TenantNoEncontrado,
    TransicionInvalida,
)
from platform_admin.slugs_reservados import es_slug_reservado

_FORMATO = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LARGO_MINIMO = 3
LARGO_MAXIMO = 40

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_NO_DATA_FOUND = "P0002"
_INVALID_PARAMETER_VALUE = "22023"
# bloqueo mutuo, conflicto de serialización y bloqueo no disponible: dos cambios se pisaron
_CONCURRENCIA = frozenset({"40P01", "40001", "55P03"})


@dataclass(frozen=True)
class ResultadoCambioSlug:
    tenant_id: uuid.UUID
    slug_anterior: str
    slug_nuevo: str


def _validar_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not _FORMATO.match(slug) or not LARGO_MINIMO <= len(slug) <= LARGO_MAXIMO:
        raise SlugInvalido(
            f"slug inválido: minúsculas, números y guiones, de {LARGO_MINIMO} a {LARGO_MAXIMO} caracteres"
        )
    if es_slug_reservado(slug):
        raise SlugReservado("slug reservado: elegí otro nombre para la dirección del negocio")
    return slug


def _traducir(exc: Exception) -> Exception | None:
    estado = getattr(exc, "sqlstate", None)
    if estado == _UNIQUE_VIOLATION:
        return SlugEnUso("Ese slug ya lo usa otro negocio o es su enlace anterior todavía vigente")
    if estado == _NO_DATA_FOUND:
        return TenantNoEncontrado("El tenant no existe")
    if estado == _INVALID_PARAMETER_VALUE:
        return SlugInvalido("slug inválido: minúsculas, números y guiones, de 3 a 40 caracteres")
    if estado == _CHECK_VIOLATION:
        return SlugReservado("slug reservado: elegí otro nombre para la dirección del negocio")
    if estado in _CONCURRENCIA:
        return CambioSlugConcurrente("Otro cambio de slug está en curso: reintentá en unos segundos")
    return None


async def cambiar_slug(pool: Any, tenant_id: Any, slug_nuevo: Any, *, actor_id: Any) -> ResultadoCambioSlug:
    """Cambia el slug del tenant con `cambiar_slug_tenant`; el viejo queda redirigiendo 180 días.

    Errores de dominio: `SlugInvalido` / `SlugReservado` (422, sin tocar la base), `TenantNoEncontrado`,
    `SlugSinCambio`, `SlugEnUso` (otro tenant, o enlace anterior vigente de otro negocio), `PurgaEnCurso` /
    `TransicionInvalida` (tenant en purga o purgado) y `CambioSlugConcurrente` (carrera con otro cambio).
    """
    slug = _validar_slug(slug_nuevo)
    try:
        async with admin_tx(pool, actor_id) as conn:
            # for update: la misma fila que bloquea la función; serializa dos cambios sobre el mismo tenant
            fila = await conn.fetchrow("select slug, status from public.tenants where id = $1 for update", tenant_id)
            if fila is None:
                raise TenantNoEncontrado("El tenant no existe")
            if fila["status"] == "purging":
                raise PurgaEnCurso("cambiar el slug de")
            if fila["status"] == "purged":
                raise TransicionInvalida("cambiar el slug de", "purged")
            if fila["slug"] == slug:
                raise SlugSinCambio("El slug ya es el vigente")
            await conn.fetchval("select public.cambiar_slug_tenant($1, $2)", tenant_id, slug)
            return ResultadoCambioSlug(tenant_id=tenant_id, slug_anterior=fila["slug"], slug_nuevo=slug)
    except ErrorDominioAdmin:
        raise
    except Exception as exc:
        traducido = _traducir(exc)
        if traducido is None:
            raise
        raise traducido from None
