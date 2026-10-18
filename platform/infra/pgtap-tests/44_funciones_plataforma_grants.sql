-- Meta-test de TODAS las funciones SECURITY DEFINER del esquema public (se recorren por pg_proc, no por una
-- lista de las que ya existen): cada una fija search_path vacio, no tiene EXECUTE para PUBLIC/anon/
-- authenticated/service_role y su EXECUTE coincide exactamente con el allowlist de abajo. Una funcion
-- SECURITY DEFINER nueva sin entrada en el allowlist hace fallar el test: quien la crea tiene que declarar
-- aca que roles de aplicacion pueden ejecutarla. Los default privileges de funciones del rol de migraciones
-- tampoco otorgan EXECUTE a nadie.
-- Sin excepciones al search_path: ninguna funcion SECURITY DEFINER necesita un search_path distinto de vacio.
begin;
select plan(13);

-- Registro de funciones SECURITY DEFINER esperadas (una entrada por nombre).
create temp table definer_esperadas (fn text primary key);
insert into definer_esperadas values
  ('custom_access_token_hook'), ('write_audit_log'), ('audit_log_append'),
  ('resolve_inbound_tenant'), ('find_integration'), ('get_integration_secret'),
  ('store_tenant_integration'), ('store_professional_integration'), ('delete_professional_integration'),
  ('pseudonymize_channel_user'), ('find_dormant_end_customer'), ('anonymize_end_customer'),
  ('record_notification_attempt'), ('consume_oauth_state_nonce'),
  ('list_tenants_due_for_purge'), ('begin_tenant_purge'), ('purge_tenant_data'), ('mark_tenant_purged'),
  ('prune_inbound_messages'), ('expire_pending_bookings'), ('prune_bot_sessions'),
  ('calendar_scrub_targets'), ('get_tenant_staff_identity'),
  ('professionals_baja_secret_calendar'), ('verify_audit_chain'),
  ('release_expired_pending_overlaps'), ('list_calendar_scrubs_pending'), ('record_calendar_scrub_result'),
  ('unlink_platform_admin_accounts'), ('discard_calendar_scrub'), ('audit_chain_heads'),
  ('enqueue_notification'), ('complete_notification_outbox'), ('claim_notification_outbox'),
  ('prune_notification_outbox'), ('resolve_outbox_delivery');

-- Allowlist de EXECUTE por rol de aplicacion.
-- Sin ningun EXECUTE (solo las alcanzan otras funciones o triggers): audit_log_append, pseudonymize_channel_user,
-- professionals_baja_secret_calendar.
create temp table esperado (fn text, rol text);
insert into esperado values
  ('custom_access_token_hook', 'supabase_auth_admin'),
  ('write_audit_log', 'bot_service'), ('write_audit_log', 'dashboard_service'),
  ('write_audit_log', 'platform_admin'), ('write_audit_log', 'purge_job'),
  ('resolve_inbound_tenant', 'bot_service'),
  ('find_integration', 'bot_service'), ('find_integration', 'dashboard_service'),
  ('find_integration', 'scheduler_job'),
  ('get_integration_secret', 'bot_service'), ('get_integration_secret', 'dashboard_service'),
  ('get_integration_secret', 'purge_job'),
  ('get_integration_secret', 'scheduler_job'),
  ('store_tenant_integration', 'platform_admin'),
  ('store_professional_integration', 'dashboard_service'),
  ('delete_professional_integration', 'dashboard_service'),
  ('find_dormant_end_customer', 'bot_service'),
  ('anonymize_end_customer', 'bot_service'), ('anonymize_end_customer', 'dashboard_service'),
  ('record_notification_attempt', 'bot_service'), ('record_notification_attempt', 'dashboard_service'),
  ('record_notification_attempt', 'scheduler_job'),
  ('consume_oauth_state_nonce', 'dashboard_service'),
  ('list_tenants_due_for_purge', 'purge_job'), ('begin_tenant_purge', 'purge_job'),
  ('purge_tenant_data', 'purge_job'), ('mark_tenant_purged', 'purge_job'),
  ('prune_inbound_messages', 'purge_job'),
  ('expire_pending_bookings', 'scheduler_job'), ('prune_bot_sessions', 'scheduler_job'),
  ('calendar_scrub_targets', 'bot_service'), ('calendar_scrub_targets', 'dashboard_service'),
  ('calendar_scrub_targets', 'platform_admin'), ('calendar_scrub_targets', 'purge_job'),
  ('get_tenant_staff_identity', 'dashboard_service'), ('get_tenant_staff_identity', 'platform_admin'),
  ('verify_audit_chain', 'platform_admin'),
  ('release_expired_pending_overlaps', 'bot_service'),
  ('list_calendar_scrubs_pending', 'purge_job'), ('record_calendar_scrub_result', 'purge_job'),
  ('unlink_platform_admin_accounts', 'purge_job'),
  ('enqueue_notification', 'bot_service'), ('enqueue_notification', 'dashboard_service'),
  ('enqueue_notification', 'scheduler_job'),
  ('complete_notification_outbox', 'bot_service'), ('complete_notification_outbox', 'dashboard_service'),
  ('complete_notification_outbox', 'scheduler_job'),
  ('claim_notification_outbox', 'scheduler_job'), ('prune_notification_outbox', 'scheduler_job'),
  ('resolve_outbox_delivery', 'scheduler_job'),
  ('discard_calendar_scrub', 'platform_admin'),
  ('audit_chain_heads', 'purge_job'), ('audit_chain_heads', 'platform_admin');

-- Vistas de deteccion sobre pg_proc: se usan sobre el esquema real y, mas abajo, sobre una funcion
-- de prueba mal hecha para comprobar que el meta-test realmente falla.
create temp view v_definer as
  select p.oid, p.proname, p.proowner, p.proconfig, p.proacl
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace and p.prosecdef;

create temp view v_sin_registro as
  select d.proname from v_definer d
   where not exists (select 1 from definer_esperadas e where e.fn = d.proname);

create temp view v_sin_search_path as
  select d.proname from v_definer d
   where not exists (select 1 from unnest(coalesce(d.proconfig, '{}')) c
                      where c in ('search_path=""', 'search_path='));

-- EXECUTE efectivo por rol (el dueño de la función no cuenta)
create temp view v_acls as
  select d.proname as fn,
         case a.grantee when 0 then 'PUBLIC' else a.grantee::regrole::text end as rol
    from v_definer d
   cross join lateral aclexplode(coalesce(d.proacl, acldefault('f', d.proowner))) a
   where a.privilege_type = 'EXECUTE'
     and a.grantee <> d.proowner;

create temp view v_acl_sobrante as
  select fn, rol from v_acls except select fn, rol from esperado;

-- Cobertura: cada SECURITY DEFINER de public esta en el registro, y cada entrada del registro existe
select is_empty($$select proname from v_sin_registro$$,
  'toda funcion SECURITY DEFINER de public figura en el allowlist (una nueva sin entrada hace fallar este test)');
select is_empty(
  $$select fn from definer_esperadas except select proname from v_definer$$,
  'toda entrada del allowlist existe y es SECURITY DEFINER');
select is_empty(
  $$select proname from v_definer group by proname having count(*) > 1$$,
  'ninguna funcion SECURITY DEFINER tiene sobrecargas que escapen al meta-test');

select is_empty($$select proname from v_sin_search_path$$,
  'toda funcion SECURITY DEFINER fija search_path vacio');

-- EXECUTE real vs allowlist, en ambos sentidos
select is_empty($$select fn, rol from v_acl_sobrante$$,
  'ninguna SECURITY DEFINER tiene EXECUTE fuera del allowlist (ni PUBLIC/anon/authenticated/service_role)');
select is_empty(
  $$select fn, rol from esperado except select fn, rol from v_acls$$,
  'cada rol del allowlist tiene el EXECUTE que le corresponde');

-- El meta-test detecta lo malo: una funcion de prueba sin search_path, con EXECUTE a PUBLIC y sin entrada en el
-- allowlist (se revierte con el rollback final).
create function public.zz_meta_test_funcion_mala() returns int
  language sql security definer as 'select 1';
grant execute on function public.zz_meta_test_funcion_mala() to public;

select isnt_empty($$select proname from v_sin_registro where proname = 'zz_meta_test_funcion_mala'$$,
  'detecta una SECURITY DEFINER sin entrada en el allowlist');
select isnt_empty($$select proname from v_sin_search_path where proname = 'zz_meta_test_funcion_mala'$$,
  'detecta una SECURITY DEFINER sin search_path');
select isnt_empty(
  $$select fn from v_acl_sobrante where fn = 'zz_meta_test_funcion_mala' and rol = 'PUBLIC'$$,
  'detecta una SECURITY DEFINER con EXECUTE a PUBLIC');

-- default privileges de funciones del rol de migraciones: nada para PUBLIC ni los roles de Supabase
select is_empty(
  $$select d.defaclrole::regrole, r.rolname
      from pg_default_acl d
     cross join lateral aclexplode(d.defaclacl) a
      left join pg_roles r on r.oid = a.grantee
     where d.defaclobjtype = 'f'
       and d.defaclrole = (select relowner from pg_class where oid = 'public.tenants'::regclass)
       and d.defaclnamespace in (0, 'public'::regnamespace)
       and (a.grantee = 0 or r.rolname in ('anon', 'authenticated', 'service_role'))$$,
  'los default privileges de funciones del rol de migraciones no otorgan EXECUTE a PUBLIC/anon/authenticated/service_role');

-- scheduler_job CONSERVA find_integration y get_integration_secret: el job de vencimiento los usa para mandar avisos
select ok(has_function_privilege('scheduler_job', 'public.find_integration(text, uuid)', 'execute')
          and has_function_privilege('scheduler_job', 'public.get_integration_secret(uuid)', 'execute'),
  'scheduler_job conserva EXECUTE sobre find_integration y get_integration_secret (job de vencimiento)');
-- purge_job conserva get_integration_secret; platform_admin ya no lo ejecuta ni anonimiza clientes
select ok(has_function_privilege('purge_job', 'public.get_integration_secret(uuid)', 'execute')
          and not has_function_privilege('platform_admin', 'public.get_integration_secret(uuid)', 'execute')
          and not has_function_privilege('platform_admin', 'public.anonymize_end_customer(uuid)', 'execute'),
  'purge_job conserva get_integration_secret; platform_admin pierde get_integration_secret y anonymize_end_customer');

-- ninguna funcion trigger de public es ejecutable por PUBLIC/anon/authenticated/service_role
select is_empty(
  $$select p.proname from pg_proc p
     cross join unnest(array['anon', 'authenticated', 'service_role']) r
     where p.pronamespace = 'public'::regnamespace and p.prorettype = 'trigger'::regtype
       and has_function_privilege(r, p.oid, 'execute')
     union all
     select p.proname from pg_proc p
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
      where p.pronamespace = 'public'::regnamespace and p.prorettype = 'trigger'::regtype and a.grantee = 0$$,
  'las funciones trigger de public no tienen EXECUTE para PUBLIC, anon, authenticated ni service_role');

select * from finish();
rollback;
