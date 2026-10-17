-- oauth_state_nonces y consume_oauth_state_nonce: nonce de un solo uso del state de OAuth de Calendar.
begin;
select plan(17);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['dashboard_service', 'bot_service', 'platform_admin'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000051a1', 'A', 'nonce-a'),
  ('00000000-0000-0000-0000-0000000051a2', 'B', 'nonce-b');

-- Ejecuta una consulta como dashboard_service con el contexto de tenant dado ('' = sin contexto).
-- Devuelve el resultado como texto o 'err <sqlstate>'.
create function pg_temp.como_dashboard(p_tenant text, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  execute 'set role dashboard_service';
  perform set_config('app.current_tenant_id', p_tenant, true);
  begin
    execute p_sql into v_res;
    reset role;
    return v_res;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

-- Estructura
select has_table('public', 'oauth_state_nonces', 'existe oauth_state_nonces');  -- 1
select ok((select relrowsecurity from pg_class where oid = 'public.oauth_state_nonces'::regclass),  -- 2
  'oauth_state_nonces tiene RLS activada');
select ok(not exists (  -- 3
  select 1 from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                             'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
   where has_any_column_privilege(r, 'public.oauth_state_nonces', 'SELECT,INSERT,UPDATE,REFERENCES')
      or has_table_privilege(r, 'public.oauth_state_nonces', 'DELETE,TRUNCATE,TRIGGER')),
  'ningun rol de aplicacion tiene privilegios directos sobre la tabla');
select ok(has_function_privilege('dashboard_service', 'public.consume_oauth_state_nonce(text, timestamptz)', 'execute')  -- 4
  and not has_function_privilege('bot_service', 'public.consume_oauth_state_nonce(text, timestamptz)', 'execute')
  and not has_function_privilege('platform_admin', 'public.consume_oauth_state_nonce(text, timestamptz)', 'execute'),
  'solo dashboard_service ejecuta la funcion');

-- Sin contexto de tenant
select is(pg_temp.como_dashboard('', $q$select public.consume_oauth_state_nonce('n-sin-ctx', now() + interval '10 minutes')$q$), 'err 42501',  -- 5
  'sin contexto de tenant lanza insufficient_privilege');

-- Primera vez true, segunda false
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('n1', now() + interval '10 minutes')$q$), 'true', 'la primera llamada devuelve true');  -- 6
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('n1', now() + interval '10 minutes')$q$), 'false',  -- 7
  'la segunda con el mismo nonce y tenant devuelve false');

-- Otro tenant: mismo valor, independiente
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a2', $q$select public.consume_oauth_state_nonce('n1', now() + interval '10 minutes')$q$), 'true', 'el mismo nonce en otro tenant no esta bloqueado');  -- 8
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a2', $q$select public.consume_oauth_state_nonce('n1', now() + interval '10 minutes')$q$), 'false', 'y alli tambien se consume una sola vez');  -- 9

-- Validaciones
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('', now() + interval '10 minutes')$q$), 'err 22023', 'nonce vacio rechazado');  -- 10
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('x', now() + interval '2 days')$q$), 'err 22023', 'vencimiento a mas de 1 dia rechazado');  -- 11
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('ya-vencido', now() + interval '-1 minute')$q$), 'false', 'un state ya vencido no se consume');  -- 12

-- La tabla no se lee directo
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', 'select count(*)::text from public.oauth_state_nonces'), 'err 42501',  -- 13
  'dashboard_service no lee la tabla directo');

-- Poda de vencidos: se insertan filas vencidas (dueño) y el siguiente consumo las elimina
insert into public.oauth_state_nonces (tenant_id, nonce, expires_at) values
  ('00000000-0000-0000-0000-0000000051a1', 'viejo-a', now() - interval '1 hour'),
  ('00000000-0000-0000-0000-0000000051a2', 'viejo-b', now() - interval '1 second');
select is((select count(*)::int from public.oauth_state_nonces where nonce like 'viejo-%'), 2,  -- 14
  'hay 2 nonces vencidos antes de consumir');
select is(pg_temp.como_dashboard('00000000-0000-0000-0000-0000000051a1', $q$select public.consume_oauth_state_nonce('n2', now() + interval '10 minutes')$q$), 'true', 'consumo que dispara la poda');  -- 15
select is((select count(*)::int from public.oauth_state_nonces where nonce like 'viejo-%'), 0,  -- 16
  'los nonces vencidos (de todos los tenants) se eliminaron al consumir');
select is((select count(*)::int from public.oauth_state_nonces where nonce in ('n1', 'n2')), 3,  -- 17
  'los nonces vigentes se conservan');

select * from finish();
rollback;
