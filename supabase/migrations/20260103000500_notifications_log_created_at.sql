-- Migración de plataforma 7: notifications_log.created_at, marca de creación de la notificación.
-- El visor de notificaciones con problemas (§2.11.7) necesita "pending hace más de 1 hora" (§6.4) y la tabla
-- solo tenía sent_at (null mientras está pending).
--
--   1. created_at timestamptz not null default now(): la pone la base. Las filas existentes reciben now()
--      (no hay dato histórico de creación).
--   2. Índice parcial de pendientes por antigüedad (consulta del visor).
--   3. Grants por columna (§3.2): bot_service y dashboard_service pasan de INSERT/UPDATE de tabla a INSERT/UPDATE
--      por columna con todas las columnas de siempre menos created_at: la aplicación no puede escribirla ni
--      falsearla. SELECT sigue por tabla (incluye la columna nueva). record_notification_attempt no la nombra.

alter table public.notifications_log
  add column created_at timestamptz not null default now();

create index notifications_log_pending_created_at_idx
  on public.notifications_log (created_at)
  where status = 'pending';

revoke insert, update on public.notifications_log from bot_service;
grant insert (id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at)
  on public.notifications_log to bot_service;
grant update (id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at)
  on public.notifications_log to bot_service;

revoke insert, update on public.notifications_log from dashboard_service;
grant insert (id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at)
  on public.notifications_log to dashboard_service;
grant update (id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at)
  on public.notifications_log to dashboard_service;
