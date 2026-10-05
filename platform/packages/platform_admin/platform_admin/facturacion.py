"""Tracking manual de pago: `tenants.paid_until` / `billing_notes` (ARCHITECTURE.md §2.11.6, §1.3).

Sin integración de cobro. Solo `platform_admin` escribe estas columnas (el dashboard del tenant no tiene grant).
El audit registra qué campos cambiaron y la fecha, pero nunca el texto de las notas.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from platform_audit import registrar
from platform_tenancy import admin_tx

from platform_admin.errores import DatosInvalidos, TenantNoEncontrado

LARGO_MAXIMO_NOTAS = 2000


class _NoTocar:
    def __repr__(self) -> str:
        return "NO_TOCAR"


NO_TOCAR: Any = _NoTocar()  # distingue "no modificar" de None ("borrar el valor")

_SELECT = "select id, paid_until, billing_notes from public.tenants where id = $1"


@dataclass(frozen=True)
class Facturacion:
    tenant_id: uuid.UUID
    paid_until: date | None
    billing_notes: str | None


async def obtener_facturacion(pool: Any, tenant_id: Any, *, actor_id: Any = None) -> Facturacion:
    async with admin_tx(pool, actor_id) as conn:
        fila = await conn.fetchrow(_SELECT, tenant_id)
    if fila is None:
        raise TenantNoEncontrado("El tenant no existe")
    return Facturacion(fila["id"], fila["paid_until"], fila["billing_notes"])


async def editar_facturacion(
    pool: Any,
    tenant_id: Any,
    *,
    actor_id: Any,
    paid_until: date | None = NO_TOCAR,
    billing_notes: str | None = NO_TOCAR,
) -> Facturacion:
    """Edita `paid_until` y/o `billing_notes`. Lo que no se pasa no se toca; `None` borra el valor."""
    campos = [n for n, v in (("paid_until", paid_until), ("billing_notes", billing_notes)) if v is not NO_TOCAR]
    if not campos:
        raise DatosInvalidos("hay que indicar paid_until y/o billing_notes")
    if paid_until is not NO_TOCAR and paid_until is not None:
        # datetime es subclase de date: se rechaza, la columna es una fecha
        if isinstance(paid_until, datetime) or not isinstance(paid_until, date):
            raise DatosInvalidos("paid_until debe ser una fecha (date)")
    if billing_notes is not NO_TOCAR and billing_notes is not None:
        if not isinstance(billing_notes, str):
            raise DatosInvalidos("billing_notes debe ser texto")
        if len(billing_notes) > LARGO_MAXIMO_NOTAS:
            raise DatosInvalidos(f"billing_notes supera {LARGO_MAXIMO_NOTAS} caracteres")

    async with admin_tx(pool, actor_id) as conn:
        fila = await conn.fetchrow(_SELECT + " for update", tenant_id)
        if fila is None:
            raise TenantNoEncontrado("El tenant no existe")
        nuevo_paid = fila["paid_until"] if paid_until is NO_TOCAR else paid_until
        nuevas_notas = fila["billing_notes"] if billing_notes is NO_TOCAR else billing_notes
        await conn.execute(
            "update public.tenants set paid_until = $2, billing_notes = $3 where id = $1",
            tenant_id,
            nuevo_paid,
            nuevas_notas,
        )
        metadata: dict[str, Any] = {"campos": campos}
        if "paid_until" in campos:
            metadata["paid_until"] = nuevo_paid.isoformat() if nuevo_paid else None
        await registrar(conn, "tenant_billing_update", "tenants", tenant_id, metadata, tenant_id=tenant_id)
    return Facturacion(tenant_id=fila["id"], paid_until=nuevo_paid, billing_notes=nuevas_notas)
