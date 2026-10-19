-- release_expired_pending_overlaps(profesional, rango): libera los pending vencidos que solapan con un rango.
-- Se ejecuta como bot_service (`set role`) con contexto de tenant, como en produccion. La concurrencia real con
-- una reserva simultanea (dos sesiones) la cubre tests/integracion/test_liberar_pendientes_vencidos.py.
begin;
select plan(32);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'scheduler_job', 'platform_admin',
                               'platform_admin_readonly', 'purge_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql (devuelve un unico texto) como p_rol con contexto de tenant p_ctx (null = sin contexto).
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

-- Consulta que resume lo liberado: booking|tenant|canal|usuario|notify|cliente, ordenado por booking
create function pg_temp.llamada(p_prof uuid, p_desde text, p_hasta text) returns text
language sql as $$
  select format($q$
    select coalesce(string_agg(r.booking_id::text || '|' || r.tenant_id::text || '|' || coalesce(r.channel, '-') || '|'
                               || coalesce(r.channel_user_id, '-') || '|' || r.notify::text || '|'
                               || coalesce(r.end_customer_id::text, '-'), ';' order by r.booking_id), '')
      from public.release_expired_pending_overlaps(%L, tstzrange(%L, %L)) r
  $q$, p_prof, p_desde, p_hasta)
$$;

-- Tenants: A activo, B activo (aislamiento), S suspendido
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'lib-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'lib-b'),
  ('00000000-0000-0000-0000-0000000000a3', 'S', 'lib-s');
update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a3';

insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'PA1'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 'PA2'),
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a2', 'PB'),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a3', 'PS');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000000e4', '00000000-0000-0000-0000-0000000000a2', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000a3', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u4'),
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'u3');
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a1');
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'manual_grant');

-- Profesional PA1, dia 2031-03-01 (cada turno de 30 min)
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at, booking_pack_id) values
  -- 601: pending vencido con pack, dentro del rango -> liberado, notify true
  ('00000000-0000-0000-0000-000000000601', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'pending', now() - interval '1 minute', '00000000-0000-0000-0000-0000000000d1'),
  -- 602: pending vigente solapado -> intacto
  ('00000000-0000-0000-0000-000000000602', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 11:00+00', '2031-03-01 11:30+00'), 'pending', now() + interval '1 hour', null),
  -- 603: confirmed solapado -> intacto
  ('00000000-0000-0000-0000-000000000603', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 12:00+00', '2031-03-01 12:30+00'), 'confirmed', null, null),
  -- 604: pending vencido FUERA del rango -> intacto
  ('00000000-0000-0000-0000-000000000604', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 15:00+00', '2031-03-01 15:30+00'), 'pending', now() - interval '1 hour', null),
  -- 605: pending vencido, cliente sin canal -> liberado, notify false
  ('00000000-0000-0000-0000-000000000605', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 13:00+00', '2031-03-01 13:30+00'), 'pending', now() - interval '1 minute', null),
  -- 606: pending vencido de OTRO profesional del mismo tenant, mismo horario que 601 -> intacto
  ('00000000-0000-0000-0000-000000000606', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b2', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'pending', now() - interval '1 minute', null),
  -- 607: pending vencido de OTRO tenant (B) -> intacto
  ('00000000-0000-0000-0000-000000000607', '00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000e4', '00000000-0000-0000-0000-0000000000b4', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'pending', now() - interval '1 minute', null),
  -- 608: pending vencido que solo roza el borde del rango (termina donde empieza el rango) -> intacto
  ('00000000-0000-0000-0000-000000000608', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 09:00+00', '2031-03-01 09:30+00'), 'pending', now() - interval '1 minute', null),
  -- 609: pending vencido en tenant suspendido -> liberado con notify false
  ('00000000-0000-0000-0000-000000000609', '00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000b3', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'pending', now() - interval '1 minute', null);

-- Aislamiento: con contexto de B no se puede operar sobre el profesional de A (error igual que inexistente)
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b1', '2031-03-01 00:00+00', '2031-03-02 00:00+00'),
  '00000000-0000-0000-0000-0000000000a2'), 'err 42501', 'con contexto de otro tenant rechaza al profesional ajeno (42501)');
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b4', '2031-03-01 00:00+00', '2031-03-02 00:00+00'),
  '00000000-0000-0000-0000-0000000000a1'), 'err 42501', 'con contexto de A rechaza al profesional de B');
select is(pg_temp.como('bot_service', pg_temp.llamada(gen_random_uuid(), '2031-03-01 00:00+00', '2031-03-02 00:00+00'),
  '00000000-0000-0000-0000-0000000000a1'), 'err 42501', 'un profesional inexistente da el mismo error');
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b1', '2031-03-01 00:00+00', '2031-03-02 00:00+00'), null),
  'err 42501', 'sin contexto de tenant rechaza');
select is((select count(*)::int from public.bookings where status = 'cancelled'), 0,
  'los rechazos no cancelaron nada');

-- rango vacio / nulo
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b1', '2031-03-01 10:00+00', '2031-03-01 10:00+00'),
  '00000000-0000-0000-0000-0000000000a1'), 'err 22023', 'un rango vacio se rechaza (22023)');
select is(pg_temp.como('bot_service',
  $q$select count(*)::text from public.release_expired_pending_overlaps('00000000-0000-0000-0000-0000000000b1', null)$q$,
  '00000000-0000-0000-0000-0000000000a1'), 'err 22023', 'un rango nulo se rechaza (22023)');

-- Rango de 09:30 a 14:00: solapa con 601, 602, 603 y 605 (608 termina a las 09:30: borde, no solapa)
create temp table lib1 as
select pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b1', '2031-03-01 09:30+00', '2031-03-01 14:00+00'),
  '00000000-0000-0000-0000-0000000000a1') as res;

select ok((select res from lib1) not like 'err%', 'bot_service la ejecuta sin error');
select is((select res from lib1),
  '00000000-0000-0000-0000-000000000601|00000000-0000-0000-0000-0000000000a1|telegram|u1|true|00000000-0000-0000-0000-0000000000c1;'
  || '00000000-0000-0000-0000-000000000605|00000000-0000-0000-0000-0000000000a1|-|-|false|-',
  'devuelve (tenant, booking, canal, usuario, notify, cliente) igual que expire_pending_bookings; sin dato personal con notify false');

select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000601'), 'cancelled', 'libera el pending vencido solapado');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000605'), 'cancelled', 'libera tambien el de cliente sin canal');
select is((select pending_expires_at from public.bookings where id = '00000000-0000-0000-0000-000000000601'), null,
  'el liberado queda sin pending_expires_at (trigger de transiciones)');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000602'), 'pending', 'no toca el pending aun vigente');
select isnt((select pending_expires_at from public.bookings where id = '00000000-0000-0000-0000-000000000602'), null,
  'el pending vigente conserva su pending_expires_at');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000603'), 'confirmed', 'no toca el confirmed');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000604'), 'pending', 'no toca el vencido fuera del rango');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000608'), 'pending', 'no toca el vencido que solo roza el borde del rango');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000606'), 'pending', 'no toca a otro profesional del mismo tenant');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000607'), 'pending', 'no toca a otro tenant');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000609'), 'pending', 'no toca al tenant suspendido');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'), 0,
  'el trigger de bookings devuelve la sesion del pack (1 -> 0)');

-- Idempotente: una segunda llamada no devuelve ni libera nada, y la sesion no se devuelve dos veces
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b1', '2031-03-01 09:30+00', '2031-03-01 14:00+00'),
  '00000000-0000-0000-0000-0000000000a1'), '', 'una segunda llamada no devuelve nada');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'), 0,
  'la sesion del pack se devolvio una sola vez');

-- Tras liberar, el horario se puede reservar sin chocar con el exclude
select lives_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
          '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 10:00+00', '2031-03-01 10:30+00'), 'confirmed')$$,
  'el horario del pending liberado ya admite una reserva nueva');
-- ...y el vigente sigue chocando
select throws_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
          '00000000-0000-0000-0000-0000000000b1', tstzrange('2031-03-01 11:00+00', '2031-03-01 11:30+00'), 'confirmed')$$,
  '23P01', null, 'el pending vigente sigue ocupando el horario');

-- starts_at y service_name del turno liberado (sin otros datos personales)
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000000701', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b2', tstzrange('2031-04-01 16:20+00', '2031-04-01 16:50+00'), 'pending', now() - interval '1 minute'),
  ('00000000-0000-0000-0000-000000000702', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b2', tstzrange('2031-04-02 09:00+00', '2031-04-02 09:30+00'), 'pending', now() - interval '1 minute');
select is(pg_temp.como('bot_service',
  $q$select string_agg(r.booking_id::text || '|' || to_char(r.starts_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI') || '|' || r.service_name || '|' || r.notify::text, ';' order by r.booking_id)
       from public.release_expired_pending_overlaps('00000000-0000-0000-0000-0000000000b2', tstzrange('2031-04-01 00:00+00', '2031-04-03 00:00+00')) r$q$,
  '00000000-0000-0000-0000-0000000000a1'),
  '00000000-0000-0000-0000-000000000701|2031-04-01 16:20|Corte|true;'
  || '00000000-0000-0000-0000-000000000702|2031-04-02 09:00|Corte|false',
  'devuelve starts_at y service_name del turno liberado; tambien con notify false');
select is(
  (select string_agg(x.nombre || ':' || format_type(x.tipo, null), ',' order by x.n)
     from pg_proc p, unnest(p.proargnames, p.proallargtypes, p.proargmodes) with ordinality as x(nombre, tipo, modo, n)
    where p.oid = 'public.release_expired_pending_overlaps(uuid, tstzrange)'::regprocedure and x.modo = 't'),
  'tenant_id:uuid,booking_id:uuid,channel:text,channel_user_id:text,notify:boolean,end_customer_id:uuid,starts_at:timestamp with time zone,service_name:text',
  'las columnas anteriores conservan nombre y orden; las nuevas van al final');

-- Tenant suspendido: libera con notify false
select is(pg_temp.como('bot_service', pg_temp.llamada('00000000-0000-0000-0000-0000000000b3', '2031-03-01 00:00+00', '2031-03-02 00:00+00'),
  '00000000-0000-0000-0000-0000000000a3'),
  '00000000-0000-0000-0000-000000000609|00000000-0000-0000-0000-0000000000a3|-|-|false|-',
  'tenant suspendido: libera con notify false y sin dato personal');

-- ---------------------------------------------------------------------------
-- Privilegios: bot_service y dashboard_service (este ultimo, ver turnos_64)
-- ---------------------------------------------------------------------------
select ok(has_function_privilege('bot_service', 'public.release_expired_pending_overlaps(uuid, tstzrange)', 'execute'),
  'bot_service tiene EXECUTE');
select is(
  (select count(*)::int
     from unnest(array['anon', 'authenticated', 'service_role', 'scheduler_job',
                       'platform_admin', 'platform_admin_readonly', 'purge_job']) r(rol)
    where has_function_privilege(r.rol, 'public.release_expired_pending_overlaps(uuid, tstzrange)', 'execute')),
  0, 'ningun otro rol de aplicacion ni de la API tiene EXECUTE (dashboard_service si, ver turnos_64)');
select is(
  (select count(*)::int from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'public.release_expired_pending_overlaps(uuid, tstzrange)'::regprocedure and a.grantee = 0),
  0, 'PUBLIC no tiene EXECUTE');
select is(
  (select count(*)::int from pg_proc p
    where p.oid = 'public.release_expired_pending_overlaps(uuid, tstzrange)'::regprocedure
      and (not p.prosecdef or p.proconfig is distinct from array['search_path=""'])),
  0, 'es SECURITY DEFINER con search_path vacio');

select * from finish();
rollback;
