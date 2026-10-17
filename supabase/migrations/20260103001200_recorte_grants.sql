-- Recorta privilegios que los roles de aplicacion no usan (minimo privilegio, §3.2).
-- Solo revoca (o agrega un EXECUTE nuevo): no toca datos y es idempotente.
--
--   platform_admin   pierde INSERT y DELETE sobre platform_admins (conserva SELECT: el admin-backend solo
--                    lee la tabla para validar la sesion; altas y bajas de admins se hacen fuera de la app)
--                    y EXECUTE sobre get_integration_secret y anonymize_end_customer.
--   bot_service      pierde todo privilegio sobre payments (los pagos los escribe otro componente).
--   dashboard_service pierde SELECT sobre audit_log (la auditoria se escribe con write_audit_log y la lee
--                    el admin).
--   triggers         las funciones trigger creadas antes de revocar los default privileges de funciones
--                    (platform 4) quedaron con EXECUTE para PUBLIC/anon/authenticated/service_role. Un trigger no
--                    necesita EXECUTE del invocador para dispararse, asi que se revoca a todas.
--   record_notification_attempt  se otorga ademas a bot_service y dashboard_service, para que registren los
--                    avisos por la funcion en lugar de escribir notifications_log directo. Los grants directos
--                    sobre notifications_log se quitan en una migracion posterior.
--
-- scheduler_job CONSERVA EXECUTE sobre find_integration y get_integration_secret: el job de vencimiento
-- (platform_notifications.credenciales, jobs_vencimiento) los usa para mandar los avisos al cliente.
-- purge_job conserva get_integration_secret (la purga borra las credenciales de las integraciones).
-- Este script no los menciona a proposito: solo se les revoca a platform_admin.

revoke insert, delete on public.platform_admins from platform_admin;

revoke execute on function public.get_integration_secret(uuid) from platform_admin;
revoke execute on function public.anonymize_end_customer(uuid) from platform_admin;

revoke all on public.payments from bot_service;

revoke select on public.audit_log from dashboard_service;

do $$
declare
  v_fn regprocedure;
begin
  for v_fn in
    select p.oid::regprocedure
      from pg_proc p
     where p.pronamespace = 'public'::regnamespace
       and p.prorettype = 'trigger'::regtype
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role', v_fn);
  end loop;
end
$$;

grant execute on function public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)
  to bot_service, dashboard_service;
