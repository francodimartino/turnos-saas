-- dashboard_service ejecuta release_expired_pending_overlaps (el alta de turno del dashboard libera los pendientes
-- vencidos solapados). El detalle fino de la función está en turnos_51; acá se prueba lo que cambia con el grant.
-- Las llamadas corren bajo el rol con pg_temp.como, que devuelve texto: las aserciones de pgTAP corren con el rol original.
begin;
select plan(13);

do $$
begin
  grant dashboard_service to current_user with inherit false, set true;
end
$$;

-- Ejecuta p_sql (un solo texto) como p_rol con contexto de tenant p_ctx (null = sin contexto).
-- Devuelve el texto o 'err <sqlstate>'. Siempre vuelve al rol original.
create function pg_temp.como(p_rol text, p_sql text, p_ctx uuid default null) returns text
language plpgsql as $$
declare
  v_res text;
begin
  perform set_config('app.current_tenant_id', coalesce(p_ctx::text, ''), true);
  execute format('set role %I', p_rol);
  begin
    execute p_sql into v_res;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
  reset role;
  return v_res;
end
$$;

create function pg_temp.liberar(p_prof uuid, p_desde text, p_hasta text) returns text
language sql as $$
  select format($q$
    select coalesce(string_agg(r.booking_id::text || '|' || r.notify::text, ';' order by r.booking_id), '')
      from public.release_expired_pending_overlaps(%L, tstzrange(%L, %L)) r
  $q$, p_prof, p_desde, p_hasta)
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'dlib-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'dlib-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'PA'),
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a2', 'PB');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');

-- Profesional PA, dia 2031-03-01 (turnos de 30 min)
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  -- 801: pending vencido solapado -> liberado
  ('00000000-0000-0000-0000-000000000801', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
   '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'pending', now() - interval '1 hour'),
  -- 802: pending vigente solapado -> intacto
  ('00000000-0000-0000-0000-000000000802', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
   '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2031-03-01 11:00+00', '2031-03-01 11:30+00'), 'pending', now() + interval '1 hour');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  -- 803: confirmed solapado -> intacto
  ('00000000-0000-0000-0000-000000000803', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
   '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2031-03-01 12:00+00', '2031-03-01 12:30+00'), 'confirmed');

-- Privilegios
select ok(has_function_privilege('dashboard_service', 'public.release_expired_pending_overlaps(uuid, tstzrange)', 'execute'),
  'dashboard_service tiene EXECUTE');  -- 1
select is(
  (select count(*)::int
     from unnest(array['anon', 'authenticated', 'service_role', 'scheduler_job', 'platform_admin',
                       'platform_admin_readonly', 'purge_job']) r(rol)
    where has_function_privilege(r.rol, 'public.release_expired_pending_overlaps(uuid, tstzrange)', 'execute')),
  0, 'ningun otro rol (salvo bot_service) tiene EXECUTE');  -- 2
select is(
  (select count(*)::int from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'public.release_expired_pending_overlaps(uuid, tstzrange)'::regprocedure and a.grantee = 0),
  0, 'PUBLIC no tiene EXECUTE');  -- 3

-- Rechazos (antes de liberar nada)
select is(pg_temp.como('dashboard_service', pg_temp.liberar('00000000-0000-0000-0000-0000000000b1', '2031-03-01 00:00+00', '2031-03-02 00:00+00'),
  '00000000-0000-0000-0000-0000000000a2'), 'err 42501', 'con contexto de otro tenant rechaza al profesional ajeno (42501)');  -- 4
select is(pg_temp.como('dashboard_service', pg_temp.liberar('00000000-0000-0000-0000-0000000000b1', '2031-03-01 00:00+00', '2031-03-02 00:00+00'), null),
  'err 42501', 'sin contexto de tenant rechaza (42501)');  -- 5
select is((select count(*)::int from public.bookings where status = 'cancelled'), 0, 'los rechazos no cancelaron nada');  -- 6

-- Liberación
create temp table lib as
select pg_temp.como('dashboard_service', pg_temp.liberar('00000000-0000-0000-0000-0000000000b1', '2031-03-01 09:30+00', '2031-03-01 13:00+00'),
  '00000000-0000-0000-0000-0000000000a1') as res;

select is((select res from lib), '00000000-0000-0000-0000-000000000801|true',
  'dashboard_service libera solo el pendiente vencido solapado y devuelve notify');  -- 7
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000801'), 'cancelled',
  'el pendiente vencido queda cancelado');  -- 8
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000802'), 'pending',
  'no toca el pendiente vigente');  -- 9
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000803'), 'confirmed',
  'no toca el confirmado');  -- 10
select is(pg_temp.como('dashboard_service', pg_temp.liberar('00000000-0000-0000-0000-0000000000b1', '2031-03-01 09:30+00', '2031-03-01 13:00+00'),
  '00000000-0000-0000-0000-0000000000a1'), '', 'una segunda llamada no devuelve nada');  -- 11

-- Tras liberar, el alta en ese horario ya no choca
select lives_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
          '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'confirmed')$$,
  'el horario liberado admite el turno nuevo');  -- 12
select is((select count(*)::int from pg_proc p
            where p.oid = 'public.release_expired_pending_overlaps(uuid, tstzrange)'::regprocedure
              and (not p.prosecdef or p.proconfig is distinct from array['search_path=""'])),
  0, 'sigue siendo SECURITY DEFINER con search_path vacio');  -- 13

select * from finish();
rollback;
