-- Quita el índice parcial viejo audit_log_calendar_scrub_idx (3 acciones de limpieza de Calendar).
-- Motivo: la migración 20260103002700 creó audit_log_calendar_scrub4_idx (4 acciones, incluye
-- calendar_scrub_discarded) y las consultas actuales usan ese; el predicado del viejo no lo implica,
-- así que ya no sirve y solo encarece cada escritura en audit_log (la tabla más grande).
-- Migración destructiva (DROP): sin cascade, aprobada con el gate de migración destructiva.
-- Se puede recrear con el mismo create index de 20260103002300 si hiciera falta volver atrás.
drop index if exists public.audit_log_calendar_scrub_idx;
