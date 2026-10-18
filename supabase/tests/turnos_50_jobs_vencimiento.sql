-- Funciones del Cron Job de vencimiento (§5.4.1, §3.2): expire_pending_bookings,
-- prune_bot_sessions y record_notification_attempt, ejecutadas como scheduler_job (`set role`).
-- Estas funciones no dependen de session_user; el caller real lo cubre
-- tests/integracion/test_turnos_jobs.py.
begin;
select plan(34);

-- postgres no es superusuario en Supabase: se le da membresía con SET dentro de la transacción.
do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['scheduler_job', 'bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql (una consulta que devuelve un único texto) como scheduler_job con contexto de
-- tenant p_ctx (null = sin contexto). Devuelve el texto o 'err <sqlstate>'. Siempre vuelve al rol original.
create function pg_temp.como_scheduler(p_sql text, p_ctx uuid default null) returns text
language plpgsql as $$
declare
  v_res text;
begin
  perform set_config('app.current_tenant_id', coalesce(p_ctx::text, ''), true);
  set role scheduler_job;
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

-- Datos: A activo, B (inactive más abajo), S suspendido
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'jobs-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'jobs-b'),
  ('00000000-0000-0000-0000-0000000000a3', 'S', 'jobs-s');
update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a3';

insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'PA'),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a3', 'PS');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000a3', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'u3');
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a1');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u4');

-- pack con 2 sesiones usadas (una por cada booking que lo usa)
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 2, 'manual_grant');

insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at, booking_pack_id) values
  -- 501: pending vencido con pack -> cancelled, notify true
  ('00000000-0000-0000-0000-000000000501', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'), 'pending', now() - interval '1 minute', '00000000-0000-0000-0000-0000000000d1'),
  -- 502: pending vencido con el mismo pack, en la misma corrida
  ('00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-02 10:00+00', '2030-10-02 10:30+00'), 'pending', now() - interval '1 hour', '00000000-0000-0000-0000-0000000000d1'),
  -- 503: pending futuro -> intacto
  ('00000000-0000-0000-0000-000000000503', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-03 10:00+00', '2030-10-03 10:30+00'), 'pending', now() + interval '1 hour', null),
  -- 504: confirmed -> intacto
  ('00000000-0000-0000-0000-000000000504', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-04 10:00+00', '2030-10-04 10:30+00'), 'confirmed', null, null),
  -- 505: pending vencido, cliente sin canal -> cancelled, notify false
  ('00000000-0000-0000-0000-000000000505', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-05 10:00+00', '2030-10-05 10:30+00'), 'pending', now() - interval '1 minute', null),
  -- 506: pending vencido de tenant suspendido -> cancelled, notify false
  ('00000000-0000-0000-0000-000000000506', '00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-10-06 10:00+00', '2030-10-06 10:30+00'), 'pending', now() - interval '1 minute', null),
  -- 507: vence exactamente ahora (<=): now() es estable dentro de la transacción
  ('00000000-0000-0000-0000-000000000507', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-07 10:00+00', '2030-10-07 10:30+00'), 'pending', now(), null);

-- Primera corrida: se resume lo devuelto sobre nuestros bookings
create temp table corrida1 as
select pg_temp.como_scheduler($q$
  select coalesce(string_agg(r.booking_id::text || '|' || r.tenant_id::text || '|' || coalesce(r.channel, '-') || '|'
                             || coalesce(r.channel_user_id, '-') || '|' || r.notify::text || '|'
                             || coalesce(r.end_customer_id::text, '-'), ';' order by r.booking_id), '')
    from public.expire_pending_bookings() r
   where r.booking_id::text like '00000000-0000-0000-0000-0000000005%'
$q$) as res;

select ok((select res from corrida1) not like 'err%', 'scheduler_job ejecuta expire_pending_bookings sin error');

select is(
  (select res from corrida1),
  '00000000-0000-0000-0000-000000000501|00000000-0000-0000-0000-0000000000a1|telegram|u1|true|00000000-0000-0000-0000-0000000000c1;'
  || '00000000-0000-0000-0000-000000000502|00000000-0000-0000-0000-0000000000a1|telegram|u1|true|00000000-0000-0000-0000-0000000000c1;'
  || '00000000-0000-0000-0000-000000000505|00000000-0000-0000-0000-0000000000a1|-|-|false|-;'
  || '00000000-0000-0000-0000-000000000506|00000000-0000-0000-0000-0000000000a3|-|-|false|-;'
  || '00000000-0000-0000-0000-000000000507|00000000-0000-0000-0000-0000000000a1|telegram|u1|true|00000000-0000-0000-0000-0000000000c1',
  'devuelve (tenant, booking, canal, usuario, notify, cliente): notify false con cliente sin canal o tenant suspendido (y sin dato personal ni end_customer_id)');

select is((select count(*)::int from public.bookings where id::text like '00000000-0000-0000-0000-0000000005%' and status = 'cancelled'), 5,
  'cancela los 5 pending vencidos (incluido el que vence en este instante)');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000503'), 'pending',
  'deja intacto el pending futuro');
select isnt((select pending_expires_at from public.bookings where id = '00000000-0000-0000-0000-000000000503'), null,
  'el pending futuro conserva su pending_expires_at');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000504'), 'confirmed',
  'deja intacto el confirmed');
select is((select count(*)::int from public.bookings where status = 'cancelled' and pending_expires_at is not null), 0,
  'los cancelados quedan sin pending_expires_at');

select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'), 0,
  'el trigger de bookings devuelve una sesión por cada booking vencido del pack (2 -> 0)');

-- Segunda corrida: idempotente, no devuelve lo ya vencido ni devuelve sesiones de nuevo
create temp table corrida2 as
select pg_temp.como_scheduler($q$
  select count(*)::text from public.expire_pending_bookings() r
   where r.booking_id::text like '00000000-0000-0000-0000-0000000005%'
$q$) as res;
select is((select res from corrida2), '0', 'una segunda corrida no devuelve nada de lo ya vencido');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'), 0,
  'la sesión del pack se devolvió una sola vez (sigue en 0)');

-- starts_at y service_name: inicio (timestamptz) y nombre del servicio del turno vencido, sin otros datos
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000000701', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-11-01 13:45+00', '2030-11-01 14:15+00'), 'pending', now() - interval '1 minute'),
  ('00000000-0000-0000-0000-000000000702', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-11-02 09:00+00', '2030-11-02 09:30+00'), 'pending', now() - interval '1 minute');
create temp table corrida3 as
select pg_temp.como_scheduler($q$
  select coalesce(string_agg(r.booking_id::text || '|' || to_char(r.starts_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI') || '|' || r.service_name || '|' || r.notify::text,
                             ';' order by r.booking_id), '')
    from public.expire_pending_bookings() r
   where r.booking_id::text like '00000000-0000-0000-0000-0000000007%'
$q$) as res;
select is((select res from corrida3),
  '00000000-0000-0000-0000-000000000701|2030-11-01 13:45|Corte|true;'
  || '00000000-0000-0000-0000-000000000702|2030-11-02 09:00|Corte|false',
  'devuelve starts_at (inicio del turno) y service_name; tambien con notify false');
select is(
  (select string_agg(x.nombre || ':' || format_type(x.tipo, null), ',' order by x.n)
     from pg_proc p, unnest(p.proargnames, p.proallargtypes, p.proargmodes) with ordinality as x(nombre, tipo, modo, n)
    where p.oid = 'public.expire_pending_bookings()'::regprocedure and x.modo = 't'),
  'tenant_id:uuid,booking_id:uuid,channel:text,channel_user_id:text,notify:boolean,end_customer_id:uuid,starts_at:timestamp with time zone,service_name:text,outbox_id:uuid',
  'las columnas anteriores conservan nombre y orden; las nuevas van al final');

-- cualquier estado de tenant distinto de active da notify false
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a2', 'PB');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e4', '00000000-0000-0000-0000-0000000000a2', 'Corte', 30, 100);
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000000508', '00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000e4', '00000000-0000-0000-0000-0000000000b4', tstzrange('2030-10-08 10:00+00', '2030-10-08 10:30+00'), 'pending', now() - interval '1 minute');
update public.tenants set status = 'inactive', deactivated_at = now() where id = '00000000-0000-0000-0000-0000000000a2';
select is(
  pg_temp.como_scheduler($q$
    select r.notify::text from public.expire_pending_bookings() r
     where r.booking_id = '00000000-0000-0000-0000-000000000508'
  $q$),
  'false', 'notify false para un tenant inactive');

-- ---------------------------------------------------------------------------
-- prune_bot_sessions
-- ---------------------------------------------------------------------------
insert into public.bot_sessions (tenant_id, channel, channel_user_id, updated_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'viejo1', now() - interval '2 hours'),
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'viejo2', now() - interval '61 minutes'),
  ('00000000-0000-0000-0000-0000000000a3', 'whatsapp', 'viejo3', now() - interval '3 days'),
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'reciente1', now() - interval '59 minutes'),
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'reciente2', now());

select cmp_ok(pg_temp.como_scheduler('select public.prune_bot_sessions()::text')::int, '>=', 3,
  'prune_bot_sessions devuelve la cantidad borrada (al menos las 3 viejas de la prueba)');
select is((select count(*)::int from public.bot_sessions where channel_user_id like 'viejo%'), 0,
  'borra las sesiones de más de 1 hora');
select is((select count(*)::int from public.bot_sessions where channel_user_id like 'reciente%'), 2,
  'deja las sesiones de menos de 1 hora');

-- ---------------------------------------------------------------------------
-- record_notification_attempt
-- ---------------------------------------------------------------------------
create temp table notif as
select pg_temp.como_scheduler($q$
  select public.record_notification_attempt(
    '00000000-0000-0000-0000-0000000000c1', 'telegram', 'turno_vencido', 'pending', 0, null)::text
$q$, '00000000-0000-0000-0000-0000000000a1') as res;
select ok((select res from notif) not like 'err%', 'registra el aviso en notifications_log con contexto de tenant');
select is((select status || '|' || tenant_id::text from public.notifications_log where id::text = (select res from notif)),
  'pending|00000000-0000-0000-0000-0000000000a1', 'la fila se crea en pending con el tenant del contexto');

select is(
  pg_temp.como_scheduler(format($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 'turno_vencido', 'sent', 1, null, %L)::text
  $q$, (select res from notif)), '00000000-0000-0000-0000-0000000000a1'),
  (select res from notif), 'actualiza el intento pending a sent');
select is((select status || '|' || retry_count::text || '|' || (sent_at is not null)::text from public.notifications_log where id::text = (select res from notif)),
  'sent|1|true', 'sent registra retry_count y sent_at');
select is(
  pg_temp.como_scheduler(format($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 'turno_vencido', 'failed', 2, 'x', %L)::text
  $q$, (select res from notif)), '00000000-0000-0000-0000-0000000000a1'),
  'err 23514', 'un estado final (sent) no se vuelve a modificar');

select is(
  pg_temp.como_scheduler($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 't', 'pending', 0, null)::text
  $q$, null),
  'err 42501', 'sin contexto de tenant rechaza');
select is(
  pg_temp.como_scheduler($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 't', 'pending', 0, null)::text
  $q$, '00000000-0000-0000-0000-0000000000a2'),
  'err 42501', 'un cliente de otro tenant que el del contexto se rechaza');
select is(
  pg_temp.como_scheduler($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 't', 'bogus', 0, null)::text
  $q$, '00000000-0000-0000-0000-0000000000a1'),
  'err 23514', 'un status fuera del check se rechaza');
select is(
  pg_temp.como_scheduler(format($q$
    select public.record_notification_attempt('00000000-0000-0000-0000-0000000000c1', 'telegram', 't', 'sent', 0, null, %L)::text
  $q$, gen_random_uuid()), '00000000-0000-0000-0000-0000000000a1'),
  'err 42501', 'actualizar una notificación inexistente se rechaza');

-- ---------------------------------------------------------------------------
-- Privilegios
-- ---------------------------------------------------------------------------
select ok(
  has_function_privilege('scheduler_job', 'public.expire_pending_bookings()', 'execute')
  and has_function_privilege('scheduler_job', 'public.prune_bot_sessions()', 'execute')
  and has_function_privilege('scheduler_job', 'public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)', 'execute')
  and has_function_privilege('scheduler_job', 'public.find_integration(text, uuid)', 'execute')
  and has_function_privilege('scheduler_job', 'public.get_integration_secret(uuid)', 'execute'),
  'scheduler_job tiene EXECUTE en las funciones de §3.2');

-- record_notification_attempt tambien la ejecutan bot_service y dashboard_service (grant agregado en recorte_grants)
select is(
  (select count(*)::int
     from unnest(array['public.expire_pending_bookings()', 'public.prune_bot_sessions()',
                       'public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)']) f(sig)
    cross join unnest(array['anon', 'authenticated', 'service_role', 'bot_service', 'dashboard_service',
                            'platform_admin', 'platform_admin_readonly', 'purge_job']) r(rol)
    where has_function_privilege(r.rol, f.sig, 'execute')
      and not (f.sig like 'public.record_notification_attempt%' and r.rol in ('bot_service', 'dashboard_service'))),
  0, 'ningún otro rol de aplicación ni de la API puede ejecutar estas funciones');

select is(
  (select count(*)::int from pg_proc p, aclexplode(p.proacl) a
    where p.pronamespace = 'public'::regnamespace
      and p.proname in ('expire_pending_bookings', 'prune_bot_sessions', 'record_notification_attempt')
      and a.grantee = 0),
  0, 'ninguna de las funciones tiene EXECUTE para PUBLIC');

select is(
  (select count(*)::int from pg_proc
    where pronamespace = 'public'::regnamespace
      and proname in ('expire_pending_bookings', 'prune_bot_sessions', 'record_notification_attempt')
      and (not prosecdef or proconfig is distinct from array['search_path=""'])),
  0, 'las tres son SECURITY DEFINER con search_path vacío');

select is(
  (select count(*)::int from pg_class c
    where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'v', 'm', 'p', 'f')
      and (has_table_privilege('scheduler_job', c.oid, 'select, insert, update, delete')
           or has_any_column_privilege('scheduler_job', c.oid, 'select, insert, update'))),
  0, 'scheduler_job no tiene ningún privilegio sobre tablas (ni SELECT)');

select is(
  pg_temp.como_scheduler('select count(*)::text from public.bookings'),
  'err 42501', 'scheduler_job no puede hacer SELECT sobre bookings');
select is(
  pg_temp.como_scheduler('select count(*)::text from public.notifications_log'),
  'err 42501', 'scheduler_job no puede hacer SELECT sobre notifications_log');

-- un rol sin EXECUTE se rechaza al llamar la función
create function pg_temp.como_rol_ejecuta(p_rol text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_rol);
  begin
    perform public.expire_pending_bookings();
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
  reset role;
  return 'ok';
end
$$;
select is(pg_temp.como_rol_ejecuta('bot_service'), 'err 42501', 'bot_service no puede ejecutar expire_pending_bookings');
select is(pg_temp.como_rol_ejecuta('purge_job'), 'err 42501', 'purge_job no puede ejecutar expire_pending_bookings');

select * from finish();
rollback;
