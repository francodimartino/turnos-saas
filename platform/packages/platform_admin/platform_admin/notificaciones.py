"""Visor de notificaciones con problemas y de limpiezas de Google Calendar fallidas (ARCHITECTURE.md §2.11.7).

Notificaciones con problemas: `status='failed'` (agotó los reintentos) o `pending` hace más de 1 hora (un
reintento perdido por un redeploy, §6.4: los reintentos normales terminan en ~30 segundos). Metadatos
operativos únicamente: `last_error` no lleva el texto enviado (§2.10).

Limpiezas de Calendar fallidas: filas de `audit_log` con `action='calendar_scrub_failed'` (§7.1), que Franco
resuelve a mano.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from platform_tenancy import admin_tx

from platform_admin.auditoria import EntradaAudit, entrada_audit, validar_paginacion

UTC = timezone.utc  # noqa: UP017  (datetime.UTC existe desde 3.11)
ACCION_SCRUB_FALLIDO = "calendar_scrub_failed"
UMBRAL_PENDING = timedelta(hours=1)
_LIMITE_POR_DEFECTO = 200

# Requiere `notifications_log.created_at`: sin una marca de creación no se puede saber hace cuánto está pending.
_SQL_NOTIFICACIONES = (
    "select id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, created_at, sent_at "
    "from public.notifications_log "
    "where status = 'failed' or (status = 'pending' and created_at < $1) "
    "order by created_at desc, id desc limit $2"
)
_SQL_SCRUBS = (
    "select id, tenant_id, actor_type, actor_id, action, entity, entity_id, metadata, created_at "
    "from public.audit_log where action = $1 order by created_at desc, id desc limit $2"
)


@dataclass(frozen=True)
class NotificacionConProblema:
    id: uuid.UUID
    tenant_id: uuid.UUID
    end_customer_id: uuid.UUID
    channel: str
    template: str | None
    status: str
    retry_count: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


@dataclass(frozen=True)
class ProblemasNotificaciones:
    notificaciones: list[NotificacionConProblema]
    limpiezas_calendar: list[EntradaAudit]


def _notificacion(fila: Any) -> NotificacionConProblema:
    return NotificacionConProblema(**{c: fila[c] for c in NotificacionConProblema.__dataclass_fields__})


async def _notificaciones(conn: Any, ahora: datetime | None, limit: int) -> list[NotificacionConProblema]:
    umbral = (ahora or datetime.now(UTC)) - UMBRAL_PENDING
    return [_notificacion(f) for f in await conn.fetch(_SQL_NOTIFICACIONES, umbral, limit)]


async def _limpiezas(conn: Any, limit: int) -> list[EntradaAudit]:
    return [entrada_audit(f) for f in await conn.fetch(_SQL_SCRUBS, ACCION_SCRUB_FALLIDO, limit)]


async def listar_notificaciones_con_problemas(
    pool: Any, *, actor_id: Any = None, ahora: datetime | None = None, limit: int = _LIMITE_POR_DEFECTO
) -> list[NotificacionConProblema]:
    """`failed` o `pending` con más de 1 hora, de todos los tenants, de la más nueva a la más vieja."""
    validar_paginacion(limit, 0)
    async with admin_tx(pool, actor_id) as conn:
        return await _notificaciones(conn, ahora, limit)


async def listar_limpiezas_calendar_fallidas(
    pool: Any, *, actor_id: Any = None, limit: int = _LIMITE_POR_DEFECTO
) -> list[EntradaAudit]:
    """Filas de `audit_log` con `action='calendar_scrub_failed'`, de todos los tenants."""
    validar_paginacion(limit, 0)
    async with admin_tx(pool, actor_id) as conn:
        return await _limpiezas(conn, limit)


async def visor_problemas_notificaciones(
    pool: Any, *, actor_id: Any = None, ahora: datetime | None = None, limit: int = _LIMITE_POR_DEFECTO
) -> ProblemasNotificaciones:
    """El visor completo de §2.11.7: ambas listas en una sola transacción (misma foto de la base)."""
    validar_paginacion(limit, 0)
    async with admin_tx(pool, actor_id) as conn:
        return ProblemasNotificaciones(
            notificaciones=await _notificaciones(conn, ahora, limit),
            limpiezas_calendar=await _limpiezas(conn, limit),
        )
