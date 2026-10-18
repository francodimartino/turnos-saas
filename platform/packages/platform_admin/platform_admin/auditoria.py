"""Visor de `audit_log` por tenant (ARCHITECTURE.md §2.11, §3.1.6).

El rol `platform_admin` lee el audit de todos los tenants (policy explícita), incluidas sus propias acciones
(`actor_type='platform_admin'`), que el tenant nunca ve. Solo lectura: el audit se escribe con `registrar`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from platform_tenancy import admin_tx

from platform_admin._json import decodificar
from platform_admin.errores import DatosInvalidos, TenantNoEncontrado

LIMITE_MAXIMO = 500
_ACTORES = frozenset({"staff", "bot", "system", "platform_admin"})
_COLUMNAS = "id, tenant_id, actor_type, actor_id, action, entity, entity_id, metadata, created_at"


@dataclass(frozen=True)
class EntradaAudit:
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    entity: str | None
    entity_id: uuid.UUID | None
    metadata: dict[str, Any] | None
    created_at: datetime


def entrada_audit(fila: Any) -> EntradaAudit:
    datos = {campo: fila[campo] for campo in EntradaAudit.__dataclass_fields__}
    datos["metadata"] = decodificar(datos["metadata"])
    return EntradaAudit(**datos)


def validar_paginacion(limit: Any, offset: Any) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIMITE_MAXIMO:
        raise DatosInvalidos(f"limit debe ser un entero entre 1 y {LIMITE_MAXIMO}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DatosInvalidos("offset debe ser un entero >= 0")


async def listar_audit_log(
    pool: Any,
    tenant_id: Any,
    *,
    actor_id: Any = None,
    action: str | None = None,
    actor_type: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[EntradaAudit]:
    """Entradas del `audit_log` de un tenant, de la más nueva a la más vieja. Filtros opcionales y paginación."""
    validar_paginacion(limit, offset)
    if action is not None and (not isinstance(action, str) or not action.strip()):
        raise DatosInvalidos("action no puede ser vacía")
    if actor_type is not None and actor_type not in _ACTORES:
        raise DatosInvalidos("actor_type inválido")
    for nombre, valor in (("desde", desde), ("hasta", hasta)):
        if valor is not None and not isinstance(valor, datetime):
            raise DatosInvalidos(f"{nombre} debe ser un datetime")

    condiciones = ["tenant_id = $1"]
    args: list[Any] = [tenant_id]
    for columna, operador, valor in (
        ("action", "=", action),
        ("actor_type", "=", actor_type),
        ("created_at", ">=", desde),
        ("created_at", "<", hasta),
    ):
        if valor is not None:
            args.append(valor)
            condiciones.append(f"{columna} {operador} ${len(args)}")
    args += [limit, offset]
    sql = (
        f"select {_COLUMNAS} from public.audit_log where {' and '.join(condiciones)} "
        f"order by created_at desc, chain_seq desc, id desc limit ${len(args) - 1} offset ${len(args)}"
    )
    async with admin_tx(pool, actor_id) as conn:
        if await conn.fetchval("select id from public.tenants where id = $1", tenant_id) is None:
            raise TenantNoEncontrado("El tenant no existe")
        filas = await conn.fetch(sql, *args)
    return [entrada_audit(f) for f in filas]


@dataclass(frozen=True)
class ResultadoCadena:
    """Resultado de verificar una cadena de hashes de `audit_log` (`tenant_id` None = cadena de plataforma)."""

    tenant_id: uuid.UUID | None
    integra: bool
    fila_invalida_id: uuid.UUID | None = None
    fila_invalida_chain_seq: int | None = None


async def verificar_cadena_audit(pool: Any, tenant_id: Any, *, actor_id: Any = None) -> ResultadoCadena:
    """Verifica la cadena de hashes del tenant (o la de plataforma, con `tenant_id=None`) con `verify_audit_chain`.

    Íntegra si la función no devuelve fila; si no, la primera fila inválida (por `chain_seq`) y su posición.
    No devuelve metadata ni datos de la fila.
    """
    async with admin_tx(pool, actor_id) as conn:
        if tenant_id is not None:
            if await conn.fetchval("select id from public.tenants where id = $1", tenant_id) is None:
                raise TenantNoEncontrado("El tenant no existe")
        fila = await conn.fetchval("select public.verify_audit_chain($1)", tenant_id)
        if fila is None:
            return ResultadoCadena(tenant_id=tenant_id, integra=True)
        posicion = await conn.fetchval("select chain_seq from public.audit_log where id = $1", fila)
    return ResultadoCadena(tenant_id=tenant_id, integra=False, fila_invalida_id=fila, fila_invalida_chain_seq=posicion)
