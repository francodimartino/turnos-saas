-- Meta-test de RLS (§3.1.1): ninguna tabla del schema public puede quedar sin RLS, y nada
-- hereda grants por defecto de Supabase (anon/authenticated/service_role/PUBLIC).
-- Los default privileges se miran para el rol que crea las tablas (el dueño de public.tenants, el de las
-- migraciones): los de supabase_admin solo rigen para objetos que cree supabase_admin.
-- Se ejecuta sobre todo el schema, incluidas las tablas de turnos: una tabla nueva sin
-- `enable row level security` hace fallar este test.
begin;
select plan(9);

select is_empty(
  $$select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind in ('r', 'p')
       and not c.relrowsecurity$$,
  'toda tabla del schema public tiene RLS activada (§3.1.1)');

select is_empty(
  $$select t
      from unnest(array[
        'tenants', 'tenant_users', 'permissions', 'tenant_user_permissions',
        'platform_settings', 'platform_admins', 'tenant_integrations', 'end_customers',
        'audit_log', 'inbound_messages', 'bot_sessions', 'notifications_log', 'payments', 'oauth_state_nonces'
      ]) t
     where not exists (
       select 1 from pg_class c
        where c.oid = to_regclass('public.' || t) and c.relrowsecurity)$$,
  'las 14 tablas de plataforma existen y tienen RLS activada');

select is_empty(
  $$select t
      from unnest(array[
        'professionals', 'services', 'availability_rules', 'availability_blocks',
        'professional_services', 'booking_packs', 'bookings'
      ]) t
     where not exists (
       select 1 from pg_class c
        where c.oid = to_regclass('public.' || t) and c.relrowsecurity)$$,
  'las 7 tablas de turnos existen y tienen RLS activada');

-- RLS sin FORCE: las funciones SECURITY DEFINER (dueño de las tablas) siguen operando.
select is_empty(
  $$select relname from pg_class
     where relnamespace = 'public'::regnamespace and relkind in ('r', 'p') and relforcerowsecurity$$,
  'ninguna tabla usa FORCE ROW LEVEL SECURITY (el dueño de las funciones definer opera sin bypassrls)');

select is_empty(
  $$select c.relname, a.privilege_type
      from pg_class c
     cross join lateral aclexplode(c.relacl) a
     where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
       and a.grantee = 0$$,
  'PUBLIC no tiene ningún grant sobre las tablas de public');

select is_empty(
  $$select c.relname, r.rolname, a.privilege_type
      from pg_class c
     cross join lateral aclexplode(c.relacl) a
      join pg_roles r on r.oid = a.grantee
     where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
       and r.rolname in ('anon', 'authenticated', 'service_role')$$,
  'anon/authenticated/service_role no conservan grants por defecto sobre las tablas de public');

select is_empty(
  $$select c.relname, at.attname, r.rolname
      from pg_class c
      join pg_attribute at on at.attrelid = c.oid and at.attacl is not null
     cross join lateral aclexplode(at.attacl) a
      join pg_roles r on r.oid = a.grantee
     where c.relnamespace = 'public'::regnamespace
       and r.rolname in ('anon', 'authenticated', 'service_role')$$,
  'anon/authenticated/service_role no tienen grants por columna en public');

select is_empty(
  $$select d.defaclrole::regrole, r.rolname
      from pg_default_acl d
     cross join lateral aclexplode(d.defaclacl) a
      left join pg_roles r on r.oid = a.grantee
     where d.defaclobjtype = 'r'
       and d.defaclrole = (select relowner from pg_class where oid = 'public.tenants'::regclass)
       and d.defaclnamespace in (0, 'public'::regnamespace)
       and (a.grantee = 0 or r.rolname in ('anon', 'authenticated', 'service_role'))$$,
  'los default privileges del rol de migraciones para tablas en public no otorgan nada a anon/authenticated/service_role/PUBLIC');

select is_empty(
  $$select d.defaclrole::regrole, r.rolname
      from pg_default_acl d
     cross join lateral aclexplode(d.defaclacl) a
      left join pg_roles r on r.oid = a.grantee
     where d.defaclobjtype = 'S'
       and d.defaclrole = (select relowner from pg_class where oid = 'public.tenants'::regclass)
       and d.defaclnamespace in (0, 'public'::regnamespace)
       and (a.grantee = 0 or r.rolname in ('anon', 'authenticated', 'service_role'))$$,
  'los default privileges de secuencias en public tampoco otorgan nada a esos roles');

select * from finish();
rollback;
