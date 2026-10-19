-- dashboard_service puede ejecutar release_expired_pending_overlaps(profesional, rango).
-- Decisión de producto: el alta de turno desde el dashboard sobre un pendiente vencido lo libera (lo cancela y avisa
-- al cliente por el outbox) y crea el turno nuevo, igual que el bot; ya no responde 409 en ese caso.
-- Migración no destructiva: solo agrega un EXECUTE. La función no cambia (ni su cuerpo ni su guard de contexto de
-- tenant, que exige app.current_tenant_id y un profesional de ese tenant: 42501 si no). Ningún otro rol se abre.
-- Mismo EXECUTE que ya tiene enqueue_notification para dashboard_service.

grant execute on function public.release_expired_pending_overlaps(uuid, tstzrange) to dashboard_service;
