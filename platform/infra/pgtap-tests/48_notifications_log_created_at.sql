-- notifications_log.created_at: marca de creación para el visor de "pending hace más de 1 hora" (§2.11.7, §6.4).
-- La pone la base (default now()); ningún rol de aplicación puede escribirla.
begin;
select plan(14);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- La columna
select has_column('public', 'notifications_log', 'created_at', 'notifications_log.created_at existe');  -- 1
select col_type_is('public', 'notifications_log', 'created_at', 'timestamp with time zone', 'es timestamptz');  -- 2
select col_not_null('public', 'notifications_log', 'created_at', 'no admite null');  -- 3
select col_default_is('public', 'notifications_log', 'created_at', 'now()', 'default now()');  -- 4

-- Índice parcial de pendientes por antigüedad
select ok(  -- 5
  exists (select 1 from pg_indexes
           where schemaname = 'public' and tablename = 'notifications_log'
             and indexname = 'notifications_log_pending_created_at_idx'
             and indexdef like '%(created_at)%' and indexdef like '%WHERE%pending%'),
  'índice parcial de pendientes por created_at');

-- Datos de prueba (rol de test, dueño de las tablas)
insert into public.tenants (id, name, slug)
  values ('00000000-0000-0000-0000-0000000048a1', 'T48', 't48-' || md5(random()::text));
insert into public.end_customers (id, tenant_id, channel, channel_user_id)
  values ('00000000-0000-0000-0000-0000000048b1', '00000000-0000-0000-0000-0000000048a1', 'telegram', 'u48');
insert into public.notifications_log (id, tenant_id, end_customer_id, channel)
  values ('00000000-0000-0000-0000-0000000048c1', '00000000-0000-0000-0000-0000000048a1',
          '00000000-0000-0000-0000-0000000048b1', 'telegram');

select ok(  -- 6
  (select created_at from public.notifications_log where id = '00000000-0000-0000-0000-0000000048c1') = now(),
  'una fila nueva recibe created_at = now() de la base');

-- Grants por columna
select is_empty(  -- 7
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated']) r
     where has_column_privilege(r, 'public.notifications_log', 'created_at', 'INSERT')
        or has_column_privilege(r, 'public.notifications_log', 'created_at', 'UPDATE')$$,
  'ningún rol de aplicación puede escribir created_at');
select ok(  -- 8
  has_column_privilege('platform_admin', 'public.notifications_log', 'created_at', 'SELECT')
  and has_column_privilege('platform_admin_readonly', 'public.notifications_log', 'created_at', 'SELECT')
  and has_column_privilege('bot_service', 'public.notifications_log', 'created_at', 'SELECT')
  and has_column_privilege('dashboard_service', 'public.notifications_log', 'created_at', 'SELECT'),
  'los roles que leían notifications_log leen created_at');
select is_empty(  -- 9
  $$select r || '.' || c || '.' || p
      from unnest(array['bot_service', 'dashboard_service']) r
     cross join unnest(array['id', 'tenant_id', 'end_customer_id', 'channel', 'template', 'status',
                             'retry_count', 'last_error', 'sent_at']) c
     cross join unnest(array['INSERT', 'UPDATE']) p
     where not has_column_privilege(r, 'public.notifications_log', c, p)$$,
  'bot_service y dashboard_service conservan INSERT/UPDATE del resto de las columnas');

create function pg_temp.como(p_rol text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  begin
    execute p_sql into v;
    reset role;
    return coalesce(v, '<null>');
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000048a1', false);

select is(pg_temp.como('bot_service',  -- 10
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel, created_at)
    values ('00000000-0000-0000-0000-0000000048a1', '00000000-0000-0000-0000-0000000048b1', 'telegram',
            now() - interval '2 hours')
    returning 'ok'$$), 'err 42501', 'bot_service no inserta created_at');
select is(pg_temp.como('dashboard_service',  -- 11
  $$update public.notifications_log set created_at = now() - interval '2 hours'
     where id = '00000000-0000-0000-0000-0000000048c1' returning 'ok'$$), 'err 42501',
  'dashboard_service no actualiza created_at');
select is(pg_temp.como('bot_service',  -- 12
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000048a1', '00000000-0000-0000-0000-0000000048b1', 'telegram')
    returning 'ok'$$), 'ok', 'bot_service sigue insertando sin created_at');
select is(pg_temp.como('bot_service',  -- 13
  $$update public.notifications_log set status = 'sent', sent_at = now()
     where id = '00000000-0000-0000-0000-0000000048c1' returning 'ok'$$), 'ok',
  'bot_service sigue actualizando status/sent_at');
select ok(  -- 14
  (select created_at from public.notifications_log where id = '00000000-0000-0000-0000-0000000048c1') = now(),
  'created_at no cambia al actualizar la notificación');

select * from finish();
rollback;
