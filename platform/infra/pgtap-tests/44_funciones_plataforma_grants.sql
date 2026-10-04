-- Meta-test de las funciones de la migración de plataforma 4 (§3.2): cada una es SECURITY DEFINER
-- con search_path fijo y su EXECUTE está otorgado exactamente a los roles de §3.2 (ni PUBLIC,
-- ni anon/authenticated/service_role). Los default privileges de funciones del rol de migraciones
-- tampoco otorgan EXECUTE a nadie.
begin;
select plan(6);

create temp table funciones (fn text primary key, definer boolean not null);
insert into funciones values
  ('custom_access_token_hook', true),
  ('write_audit_log', true),
  ('audit_log_append', true),
  ('audit_actor_type', false),
  ('resolve_inbound_tenant', true),
  ('find_integration', true),
  ('get_integration_secret', true),
  ('store_tenant_integration', true),
  ('store_professional_integration', true),
  ('delete_professional_integration', true),
  ('pseudonymize_channel_user', true),
  ('find_dormant_end_customer', true);

create temp table esperado (fn text, rol text);
insert into esperado values
  ('custom_access_token_hook', 'supabase_auth_admin'),
  ('write_audit_log', 'bot_service'), ('write_audit_log', 'dashboard_service'),
  ('write_audit_log', 'platform_admin'), ('write_audit_log', 'purge_job'),
  ('resolve_inbound_tenant', 'bot_service'),
  ('find_integration', 'bot_service'), ('find_integration', 'dashboard_service'),
  ('find_integration', 'scheduler_job'),
  ('get_integration_secret', 'bot_service'), ('get_integration_secret', 'dashboard_service'),
  ('get_integration_secret', 'platform_admin'), ('get_integration_secret', 'purge_job'),
  ('get_integration_secret', 'scheduler_job'),
  ('store_tenant_integration', 'platform_admin'),
  ('store_professional_integration', 'dashboard_service'),
  ('delete_professional_integration', 'dashboard_service'),
  ('find_dormant_end_customer', 'bot_service');
-- sin ningún EXECUTE para roles de aplicación: audit_log_append, audit_actor_type, pseudonymize_channel_user

-- Cada función existe una sola vez (sin sobrecargas que escapen a este meta-test)
select is_empty(
  $$select f.fn from funciones f
     where (select count(*) from pg_proc p
             where p.pronamespace = 'public'::regnamespace and p.proname = f.fn) <> 1$$,
  'las 12 funciones existen, una sola firma cada una');

select is_empty(
  $$select p.proname from pg_proc p join funciones f on f.fn = p.proname
     where p.pronamespace = 'public'::regnamespace and p.prosecdef is distinct from f.definer$$,
  'las funciones de acceso a datos son SECURITY DEFINER');

select is_empty(
  $$select p.proname from pg_proc p join funciones f on f.fn = p.proname
     where p.pronamespace = 'public'::regnamespace
       and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c where c like 'search_path=%')$$,
  'todas fijan search_path');

-- EXECUTE real vs esperado, en ambos sentidos (el dueño de la función no cuenta)
create temp table acls as
  select p.proname as fn,
         case a.grantee when 0 then 'PUBLIC' else a.grantee::regrole::text end as rol
    from pg_proc p
    join funciones f on f.fn = p.proname
   cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
   where p.pronamespace = 'public'::regnamespace
     and a.privilege_type = 'EXECUTE'
     and a.grantee <> p.proowner;

select is_empty(
  $$select fn, rol from acls except select fn, rol from esperado$$,
  'ninguna función tiene EXECUTE para un rol que no figura en §3.2 (ni PUBLIC/anon/authenticated/service_role)');
select is_empty(
  $$select fn, rol from esperado except select fn, rol from acls$$,
  'cada rol de §3.2 tiene el EXECUTE que le corresponde');

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

select * from finish();
rollback;
