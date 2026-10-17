-- Migración de plataforma: notifications_log solo se escribe por record_notification_attempt (§3.2, minimo privilegio).
--
-- bot_service y dashboard_service registran los avisos al cliente por la funcion record_notification_attempt
-- (EXECUTE otorgado en 20260103001200_recorte_grants.sql; el codigo ya no escribe la tabla directo). Esta migracion
-- les quita los privilegios de escritura directa que quedaron:
--
--   - INSERT y UPDATE sobre notifications_log, que desde 20260103000500 eran por columna (todas menos created_at).
--     Revocar a nivel de tabla tambien revoca los privilegios de columna.
--   - Conservan SELECT (el bot y el dashboard leen el estado de los avisos; RLS los acota a su tenant).
--
-- La funcion es security definer: sigue funcionando para ambos roles. Solo revoca; es idempotente y no toca datos.

revoke insert, update on public.notifications_log from bot_service, dashboard_service;
