-- Vencimiento + outbox (§6.4): expire_pending_bookings encola el aviso en la misma sentencia que cancela el turno,
-- y resolve_outbox_delivery entrega al job lo necesario para enviar una fila reclamada.
begin;
select plan(12);

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

-- Ejecuta una consulta (un unico texto) como un rol. Devuelve el texto o 'err <sqlstate>'.
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

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000061a1', 'A', 'venc-outbox-a');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000061b1', '00000000-0000-0000-0000-0000000061a1', 'Ana');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000061e1', '00000000-0000-0000-0000-0000000061a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000061c1', '00000000-0000-0000-0000-0000000061a1', 'telegram', 'v-c1');
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000061c2', '00000000-0000-0000-0000-0000000061a1');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status,
                             pending_expires_at) values
  ('00000000-0000-0000-0000-000000006101', '00000000-0000-0000-0000-0000000061a1',
   '00000000-0000-0000-0000-0000000061c1', '00000000-0000-0000-0000-0000000061e1',
   '00000000-0000-0000-0000-0000000061b1', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'),
   'pending', now() - interval '1 minute'),
  ('00000000-0000-0000-0000-000000006102', '00000000-0000-0000-0000-0000000061a1',
   '00000000-0000-0000-0000-0000000061c2', '00000000-0000-0000-0000-0000000061e1',
   '00000000-0000-0000-0000-0000000061b1', tstzrange('2030-10-02 10:00+00', '2030-10-02 10:30+00'),
   'pending', now() - interval '1 minute');

-- Estructura y permisos
select ok(  -- 1
  has_function_privilege('scheduler_job', 'public.resolve_outbox_delivery(uuid)', 'execute')
  and not has_function_privilege('bot_service', 'public.resolve_outbox_delivery(uuid)', 'execute')
  and not has_function_privilege('dashboard_service', 'public.resolve_outbox_delivery(uuid)', 'execute')
  and not has_function_privilege('purge_job', 'public.resolve_outbox_delivery(uuid)', 'execute'),
  'resolve_outbox_delivery: solo scheduler_job');
select is(  -- 2
  (select string_agg(x.nombre, ',' order by x.n)
     from pg_proc p, unnest(p.proargnames, p.proargmodes) with ordinality as x(nombre, modo, n)
    where p.oid = 'public.expire_pending_bookings()'::regprocedure and x.modo = 't'),
  'tenant_id,booking_id,channel,channel_user_id,notify,end_customer_id,starts_at,service_name,outbox_id',
  'expire_pending_bookings devuelve outbox_id al final');

-- Primera corrida
create temp table corrida as
select pg_temp.como('scheduler_job', $q$
  select string_agg(r.booking_id::text || '|' || r.notify::text || '|' || (r.outbox_id is not null)::text, ';'
                    order by r.booking_id)
    from public.expire_pending_bookings() r
   where r.booking_id::text like '00000000-0000-0000-0000-0000000061%'
$q$) as res;
select is((select res from corrida),  -- 3
  '00000000-0000-0000-0000-000000006101|true|true;00000000-0000-0000-0000-000000006102|false|false',
  'solo el turno con aviso trae outbox_id');
select is((select count(*)::int from public.notification_outbox  -- 4
            where template = 'turno_vencido' and tenant_id = '00000000-0000-0000-0000-0000000061a1'), 1,
  'se encola exactamente una fila (nada para el cliente sin canal)');
select is((select status || ':' || attempts || ':' || (params ->> 'booking_id') || ':' || end_customer_id::text  -- 5
             from public.notification_outbox where template = 'turno_vencido'),
  'pending:0:00000000-0000-0000-0000-000000006101:00000000-0000-0000-0000-0000000061c1',
  'la fila nace pending, sin intentos y con el turno en params');
select ok((select next_attempt_at > now() + interval '4 minutes' and next_attempt_at <= now() + interval '6 minutes'  -- 6
             from public.notification_outbox where template = 'turno_vencido'),
  'el primer intento queda a 5 minutos (el job envia de inmediato y cierra la fila)');
select is((select count(*)::int from public.expire_pending_bookings() r  -- 7
            where r.booking_id::text like '00000000-0000-0000-0000-0000000061%'), 0,
  'una segunda corrida no vuelve a encolar');

-- resolve_outbox_delivery
select is(pg_temp.como('scheduler_job', format(  -- 8
  $q$select r.channel_user_id || '|' || r.service_name || '|' || r.professional_name || '|' || r.booking_status
            || '|' || r.tenant_active::text || '|' || to_char(r.starts_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI')
       from public.resolve_outbox_delivery(%L) r$q$,
  (select id from public.notification_outbox where template = 'turno_vencido'))),
  'v-c1|Corte|Ana|cancelled|true|2030-10-01 10:00', 'devuelve destinatario, turno y estado del tenant');
select is(pg_temp.como('bot_service', format($q$select count(*)::text from public.resolve_outbox_delivery(%L)$q$,  -- 9
  (select id from public.notification_outbox where template = 'turno_vencido'))),
  'err 42501', 'otro rol no puede ejecutarla');
select is(pg_temp.como('scheduler_job',  -- 10
  $q$select count(*)::text from public.resolve_outbox_delivery('00000000-0000-0000-0000-00000000dead')$q$),
  '0', 'un id inexistente no devuelve nada');

-- una fila ya cerrada no se resuelve
update public.notification_outbox set status = 'sent', processed_at = now() where template = 'turno_vencido';
select is(pg_temp.como('scheduler_job', format($q$select count(*)::text from public.resolve_outbox_delivery(%L)$q$,  -- 11
  (select id from public.notification_outbox where template = 'turno_vencido'))),
  '0', 'una fila sent no se resuelve');

-- cliente anonimizado: sin destinatario
update public.notification_outbox set status = 'pending', processed_at = null where template = 'turno_vencido';
update public.end_customers set deleted_at = now() where id = '00000000-0000-0000-0000-0000000061c1';
select is(pg_temp.como('scheduler_job', format(  -- 12
  $q$select coalesce(r.channel_user_id, '<sin destinatario>') from public.resolve_outbox_delivery(%L) r$q$,
  (select id from public.notification_outbox where template = 'turno_vencido'))),
  '<sin destinatario>', 'un cliente anonimizado no tiene destinatario');

select * from finish();
rollback;
