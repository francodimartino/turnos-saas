-- Grants exactos por rol sobre las tablas de plataforma (§3.2).
-- Compara, para cada rol de login y cada tabla, el conjunto completo de privilegios de tabla
-- contra el esperado: cualquier grant de más (o de menos) hace fallar el test.
begin;

create temp table esperado (rol text, tabla text, privs text);
-- privilegios de tabla esperados, en el orden SELECT,INSERT,UPDATE,DELETE; lo que no figura = ninguno.
-- bot_service no tiene nada sobre payments ni dashboard_service sobre audit_log; platform_admin solo lee
-- platform_admins.
-- Los grants por columna (tenants.settings, tenant_integrations, tenant_users para dashboard_service y
-- platform_admin_readonly: todas menos pending_invite_email; se verifican más abajo y en 47 y 48.
-- oauth_state_nonces: ningun rol tiene privilegios de tabla (dashboard_service solo ejecuta consume_oauth_state_nonce, 51).
-- notification_outbox: ningun rol tiene privilegios de tabla (solo funciones enqueue/complete/claim/prune, 59).
-- notifications_log: bot_service y dashboard_service solo leen; escriben por record_notification_attempt (48).
insert into esperado values
  ('bot_service', 'end_customers', 'SELECT,INSERT,UPDATE'),
  ('bot_service', 'notifications_log', 'SELECT'),
  ('bot_service', 'inbound_messages', 'INSERT'),
  ('bot_service', 'bot_sessions', 'SELECT,INSERT,UPDATE,DELETE'),
  ('bot_service', 'tenants', 'SELECT'),
  ('dashboard_service', 'tenants', 'SELECT'),
  ('dashboard_service', 'tenant_user_permissions', 'SELECT,INSERT,DELETE'),
  ('dashboard_service', 'permissions', 'SELECT'),
  ('dashboard_service', 'end_customers', 'SELECT,INSERT,UPDATE'),
  ('dashboard_service', 'notifications_log', 'SELECT'),
  ('platform_admin', 'tenants', 'SELECT,INSERT,UPDATE'),
  ('platform_admin', 'platform_settings', 'SELECT,INSERT,UPDATE'),
  ('platform_admin', 'platform_admins', 'SELECT'),
  ('platform_admin', 'tenant_users', 'SELECT,INSERT,UPDATE'),
  ('platform_admin', 'tenant_integrations', 'SELECT'),
  ('platform_admin', 'audit_log', 'SELECT'),
  ('platform_admin', 'notifications_log', 'SELECT'),
  ('platform_admin_readonly', 'tenants', 'SELECT'),
  ('platform_admin_readonly', 'permissions', 'SELECT'),
  ('platform_admin_readonly', 'tenant_user_permissions', 'SELECT'),
  ('platform_admin_readonly', 'end_customers', 'SELECT'),
  ('platform_admin_readonly', 'notifications_log', 'SELECT');

create temp table casos as
select
  row_number() over (order by r.rol, t.tabla) as n,
  r.rol || ' sobre ' || t.tabla as descr,
  coalesce(e.privs, '') as esperado,
  coalesce((
    select string_agg(p, ',' order by array_position(
             array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'], p))
      from unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) p
     where has_table_privilege(r.rol, 'public.' || t.tabla, p)
  ), '') as real
from unnest(array['bot_service', 'dashboard_service', 'platform_admin',
                  'platform_admin_readonly', 'purge_job', 'scheduler_job']) as r(rol)
cross join unnest(array[
  'tenants', 'tenant_users', 'permissions', 'tenant_user_permissions', 'platform_settings',
  'platform_admins', 'tenant_integrations', 'end_customers', 'audit_log', 'inbound_messages',
  'bot_sessions', 'notifications_log', 'payments', 'oauth_state_nonces', 'notification_outbox']) as t(tabla)
left join esperado e on e.rol = r.rol and e.tabla = t.tabla;

-- 6 roles x 15 tablas + 16 chequeos puntuales
select plan((select count(*)::int from casos) + 16);

select is(real, esperado, 'privilegios de tabla: ' || descr) from casos order by n;

-- Por columna (§3.2): dashboard_service solo actualiza tenants.settings
select is(
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.tenants'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('dashboard_service', attrelid, attnum, 'UPDATE')),
  'settings', 'dashboard_service: UPDATE solo de la columna tenants.settings');
select ok(not has_column_privilege('dashboard_service', 'public.tenants', 'status', 'UPDATE'),
  'dashboard_service no puede actualizar tenants.status');
select ok(not has_column_privilege('dashboard_service', 'public.tenants', 'paid_until', 'UPDATE'),
  'dashboard_service no puede actualizar tenants.paid_until');
select ok(not has_column_privilege('dashboard_service', 'public.tenants', 'billing_notes', 'UPDATE'),
  'dashboard_service no puede actualizar tenants.billing_notes');
select ok(not has_column_privilege('dashboard_service', 'public.tenants', 'plan', 'UPDATE'),
  'dashboard_service no puede actualizar tenants.plan');
select ok(not has_column_privilege('dashboard_service', 'public.tenants', 'deactivated_at', 'UPDATE'),
  'dashboard_service no puede actualizar tenants.deactivated_at');

-- notifications_log: ni privilegio de tabla ni de columna de escritura para los roles de aplicación
select ok(not has_any_column_privilege('bot_service', 'public.notifications_log', 'INSERT,UPDATE')
          and not has_any_column_privilege('dashboard_service', 'public.notifications_log', 'INSERT,UPDATE'),
  'bot_service y dashboard_service no escriben notifications_log (ni por columna)');

-- tenant_integrations: SELECT sin secret_id/config para dashboard_service y platform_admin_readonly
select is(
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.tenant_integrations'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('dashboard_service', attrelid, attnum, 'SELECT')),
  'id,tenant_id,integration_type,professional_id,created_at',
  'dashboard_service: SELECT de tenant_integrations sin secret_id ni config');
select is(
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.tenant_integrations'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('platform_admin_readonly', attrelid, attnum, 'SELECT')),
  'id,tenant_id,integration_type,professional_id,created_at',
  'platform_admin_readonly: SELECT de tenant_integrations sin secret_id ni config');
select ok(not has_any_column_privilege('bot_service', 'public.tenant_integrations', 'SELECT'),
  'bot_service no tiene SELECT sobre ninguna columna de tenant_integrations');
select ok(not has_any_column_privilege('dashboard_service', 'public.tenant_integrations', 'INSERT,UPDATE'),
  'dashboard_service no escribe tenant_integrations (ni por columna)');

-- purge_job y scheduler_job: sin ningún privilegio, ni de tabla ni de columna, sobre tablas de public
select is(
  (select count(*) from pg_class c cross join unnest(array['purge_job', 'scheduler_job']) r
    where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
      and (has_table_privilege(r, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
           or has_any_column_privilege(r, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))),
  0::bigint, 'purge_job y scheduler_job no tienen ningún privilegio sobre tablas de public');
select ok(not has_schema_privilege('purge_job', 'public', 'CREATE')
          and not has_schema_privilege('scheduler_job', 'public', 'CREATE'),
  'purge_job y scheduler_job no pueden crear objetos en public');
select ok(has_schema_privilege('purge_job', 'public', 'USAGE')
          and has_schema_privilege('scheduler_job', 'public', 'USAGE'),
  'purge_job y scheduler_job conservan USAGE en public (para ejecutar sus funciones definer)');

-- Esquema: ningún rol de aplicación crea objetos en public, todos pueden usarlo
select ok(not (has_schema_privilege('bot_service', 'public', 'CREATE')
            or has_schema_privilege('dashboard_service', 'public', 'CREATE')
            or has_schema_privilege('platform_admin', 'public', 'CREATE')
            or has_schema_privilege('platform_admin_readonly', 'public', 'CREATE')),
  'ningún rol de aplicación puede crear objetos en public');
select ok(has_schema_privilege('bot_service', 'public', 'USAGE')
          and has_schema_privilege('dashboard_service', 'public', 'USAGE')
          and has_schema_privilege('platform_admin', 'public', 'USAGE')
          and has_schema_privilege('platform_admin_readonly', 'public', 'USAGE'),
  'los roles de aplicación tienen USAGE en public');

select * from finish();
rollback;
