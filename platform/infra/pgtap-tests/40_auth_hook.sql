-- Auth Hook custom_access_token_hook (§3.4): claims tenant_id / is_owner / is_platform_admin
-- en tres casos (staff activo, staff desactivado, Franco sin fila en tenant_users) y privilegios.
begin;
select plan(22);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'hook-a');
insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000c1'),  -- owner activo
  ('00000000-0000-0000-0000-0000000000c2'),  -- staff activo (no owner)
  ('00000000-0000-0000-0000-0000000000c3'),  -- staff desactivado
  ('00000000-0000-0000-0000-0000000000c4'),  -- Franco: platform_admin sin fila en tenant_users
  ('00000000-0000-0000-0000-0000000000c5');  -- cuenta sin ninguna fila
insert into public.tenant_users (tenant_id, auth_user_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', true, true),
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', false, true),
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c3', false, false);
insert into public.platform_admins (auth_user_id) values
  ('00000000-0000-0000-0000-0000000000c4');

create function pg_temp.hook(p_user uuid) returns jsonb language sql as $$
  select public.custom_access_token_hook(jsonb_build_object(
    'user_id', p_user,
    'claims', jsonb_build_object('sub', p_user, 'aud', 'authenticated', 'role', 'authenticated')))
$$;

-- Staff activo (owner)
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c1') -> 'claims' ->> 'tenant_id',
  '00000000-0000-0000-0000-0000000000a1', 'owner activo: tenant_id del tenant');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c1') -> 'claims' -> 'is_owner',
  'true'::jsonb, 'owner activo: is_owner = true');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c1') -> 'claims' -> 'is_platform_admin',
  'false'::jsonb, 'owner activo: is_platform_admin = false');

-- Staff activo (no owner)
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c2') -> 'claims' ->> 'tenant_id',
  '00000000-0000-0000-0000-0000000000a1', 'staff activo: tenant_id del tenant');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c2') -> 'claims' -> 'is_owner',
  'false'::jsonb, 'staff activo no owner: is_owner = false');

-- Staff desactivado: sin tenant (null de JSON, nunca el claim ausente ni el evento NULL)
select isnt(pg_temp.hook('00000000-0000-0000-0000-0000000000c3'), null,
  'staff desactivado: el hook devuelve un evento (no NULL)');
select is(jsonb_typeof(pg_temp.hook('00000000-0000-0000-0000-0000000000c3') -> 'claims' -> 'tenant_id'),
  'null', 'staff desactivado: tenant_id es null de JSON');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c3') -> 'claims' -> 'is_owner',
  'false'::jsonb, 'staff desactivado: is_owner = false');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c3') -> 'claims' -> 'is_platform_admin',
  'false'::jsonb, 'staff desactivado: is_platform_admin = false');

-- Franco: platform_admin sin fila en tenant_users
select isnt(pg_temp.hook('00000000-0000-0000-0000-0000000000c4'), null,
  'Franco sin fila en tenant_users: el hook devuelve un evento (no NULL)');
select is(jsonb_typeof(pg_temp.hook('00000000-0000-0000-0000-0000000000c4') -> 'claims' -> 'tenant_id'),
  'null', 'Franco: tenant_id es null de JSON');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c4') -> 'claims' -> 'is_owner',
  'false'::jsonb, 'Franco: is_owner = false');
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c4') -> 'claims' -> 'is_platform_admin',
  'true'::jsonb, 'Franco: is_platform_admin = true');

-- Cuenta sin ninguna fila: claims completos igual
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c5') -> 'claims' -> 'is_platform_admin',
  'false'::jsonb, 'cuenta sin filas: is_platform_admin = false');

-- Los claims nunca son NULL de SQL en ninguno de los casos
select is_empty(
  $$select u from unnest(array[
      '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000c2',
      '00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000c4',
      '00000000-0000-0000-0000-0000000000c5']::uuid[]) u
     where pg_temp.hook(u) is null
        or pg_temp.hook(u) -> 'claims' -> 'tenant_id' is null
        or pg_temp.hook(u) -> 'claims' -> 'is_owner' is null
        or pg_temp.hook(u) -> 'claims' -> 'is_platform_admin' is null$$,
  'tenant_id, is_owner e is_platform_admin nunca son NULL (claims presentes en los 5 casos)');

-- Conserva los claims que ya traía el evento
select is(pg_temp.hook('00000000-0000-0000-0000-0000000000c1') -> 'claims' ->> 'aud',
  'authenticated', 'el hook conserva los claims originales del token');

-- Definición: SECURITY DEFINER con search_path fijo
select ok((select p.prosecdef from pg_proc p where p.oid = 'public.custom_access_token_hook(jsonb)'::regprocedure),
  'custom_access_token_hook es SECURITY DEFINER');
select ok((select exists (select 1 from unnest(p.proconfig) c where c like 'search_path=%')
             from pg_proc p where p.oid = 'public.custom_access_token_hook(jsonb)'::regprocedure),
  'custom_access_token_hook fija search_path');

-- EXECUTE solo para supabase_auth_admin
select ok(has_function_privilege('supabase_auth_admin', 'public.custom_access_token_hook(jsonb)', 'execute'),
  'supabase_auth_admin puede ejecutar el hook');
select is_empty(
  $$select r from unnest(array['anon', 'authenticated', 'service_role', 'bot_service', 'dashboard_service',
                               'platform_admin', 'platform_admin_readonly', 'purge_job', 'scheduler_job']) r
     where has_function_privilege(r, 'public.custom_access_token_hook(jsonb)', 'execute')$$,
  'ningún otro rol (anon/authenticated/service_role/roles de aplicación) ejecuta el hook');
select is_empty(
  $$select 1 from pg_proc p cross join lateral aclexplode(p.proacl) a
     where p.oid = 'public.custom_access_token_hook(jsonb)'::regprocedure and a.grantee = 0$$,
  'PUBLIC no tiene EXECUTE sobre el hook');
select ok(has_schema_privilege('supabase_auth_admin', 'public', 'usage'),
  'supabase_auth_admin tiene USAGE sobre el schema public (necesario para invocar el hook)');

select * from finish();
rollback;
