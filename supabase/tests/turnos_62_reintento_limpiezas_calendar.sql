-- list_calendar_scrubs_pending / record_calendar_scrub_result: seguimiento y reintento de calendar_scrub_failed.
-- Se ejecutan como purge_job (`set role`), como en produccion; el resto de roles no puede.
begin;
select plan(30);

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

-- Ejecuta p_sql (devuelve un unico texto) como p_rol. Devuelve el texto o 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
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

-- Resumen de lo pendiente: tenant|evento|intentos|integracion, ordenado
create function pg_temp.pendientes() returns text
language sql as $$
  select pg_temp.como('purge_job', $q$
    select coalesce(string_agg(right(r.tenant_id::text, 2) || '|' || r.event_id || '|' || r.attempts || '|'
                               || coalesce(right(r.integration_id::text, 2), '-'), ';'
                               order by r.tenant_id, r.event_id), '')
      from public.list_calendar_scrubs_pending() r
  $q$)
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'scrub-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'scrub-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'PA');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');
insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'google_calendar',
   '00000000-0000-0000-0000-0000000000b1');
insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status,
                             google_calendar_event_id) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
   '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'), 'confirmed', 'evz_sin_integ');

-- Fallos iniciales: ev1 (con integration_id en la metadata), ev2 (sin integracion resoluble), evz_sin_integ
-- (la metadata no trae integracion: se resuelve por el booking), ev-b del tenant B (integration_id basura), y
-- filas ajenas que no deben contar (otra accion, y un fallo sin event_id).
insert into public.audit_log (tenant_id, actor_type, action, entity, metadata) values
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "ev1", "integration_id": "00000000-0000-0000-0000-0000000000f1", "motivo": "http_500"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'bot', 'calendar_scrub_failed', null,
   '{"event_id": "ev2", "motivo": "sin_integracion"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'calendar_scrub_failed', 'end_customers',
   '{"event_id": "evz_sin_integ", "motivo": "no_configurado"}'),
  ('00000000-0000-0000-0000-0000000000a2', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "ev-b", "integration_id": "no-es-uuid", "motivo": "timeout"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'tenant_purge_step', 'tenants', '{"event_id": "ev-ajeno"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"motivo": "sin event_id"}');

select is(pg_temp.pendientes(),  -- 1
  '01|ev1|1|f1;01|ev2|1|-;01|evz_sin_integ|1|f1;02|ev-b|1|-',
  'lista un evento por limpieza pendiente: intentos 1, integracion de la metadata o resuelta por el booking; ignora filas ajenas');

select is((select count(*) from public.audit_log where action = 'calendar_scrub_failed'), 5::bigint,  -- 2
  'precondicion: 5 fallos registrados (uno sin event_id)');

-- Permisos
select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending()'), '4',  -- 3
  'purge_job lista');
select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending(1)'), '1',  -- 4
  'p_limit limita las filas');
select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending(0)'), 'err 22023',  -- 5
  'p_limit 0 se rechaza');
select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending(1001)'),  -- 6
  'err 22023', 'p_limit 1001 se rechaza');

select is(  -- 7
  (select string_agg(r.rol, ',' order by r.rol)
     from unnest(array['bot_service', 'dashboard_service', 'scheduler_job', 'platform_admin',
                       'platform_admin_readonly', 'anon', 'authenticated', 'service_role']) r(rol)
    where has_function_privilege(r.rol, 'public.list_calendar_scrubs_pending(integer)', 'execute')
       or has_function_privilege(r.rol, 'public.record_calendar_scrub_result(uuid, text, boolean, text)', 'execute')),
  null, 'ningun otro rol (ni dashboard/bot/platform_admin) puede ejecutarlas');
select ok(has_function_privilege('purge_job', 'public.list_calendar_scrubs_pending(integer)', 'execute')  -- 8
          and has_function_privilege('purge_job', 'public.record_calendar_scrub_result(uuid, text, boolean, text)',
                                     'execute'), 'purge_job puede ejecutar ambas');
select is(pg_temp.como('bot_service', 'select count(*)::text from public.list_calendar_scrubs_pending()'),  -- 9
  'err 42501', 'bot_service no lista');
select is(pg_temp.como('dashboard_service',  -- 10
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev1', true)::text$$),
  'err 42501', 'dashboard_service no registra resultados');
select ok(not exists (select 1 from pg_proc p where p.proname = 'calendar_scrubs_pending_core'  -- 11
                        and (has_function_privilege('purge_job', p.oid, 'execute')
                             or has_function_privilege('authenticated', p.oid, 'execute'))),
  'el nucleo interno no es ejecutable por los roles de aplicacion');

-- Registrar un reintento fallido
select is(pg_temp.como('purge_job',  -- 12
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev1', false, 'http_503')::text$$),
  '', 'reintento fallido se registra (void)');
select is((select metadata from public.audit_log where action = 'calendar_scrub_retry_failed'),  -- 13
  '{"event_id": "ev1", "integration_id": "00000000-0000-0000-0000-0000000000f1", "motivo": "http_503", "attempt": 2}'::jsonb,
  'la fila de reintento lleva solo ids, motivo e intento');
select is((select actor_type from public.audit_log where action = 'calendar_scrub_retry_failed'), 'system',  -- 14
  'actor_type system');
select is((select count(*) from public.audit_log where action = 'calendar_scrub_retry_failed' and hash is not null
            and chain_seq is not null), 1::bigint, 'la fila entra en la cadena de hashes del audit_log');  -- 15
select is(pg_temp.pendientes(),  -- 16
  '01|ev1|2|f1;01|ev2|1|-;01|evz_sin_integ|1|f1;02|ev-b|1|-',
  'ev1 sigue pendiente con 2 intentos');
select is(pg_temp.como('purge_job',  -- 17
  $$select last_motivo from public.list_calendar_scrubs_pending() where event_id = 'ev1'$$), 'http_503',
  'last_motivo es el del ultimo fallo');

-- Motivo obligatorio y recortado
select is(pg_temp.como('purge_job',  -- 18
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev2', false, '  ')::text$$),
  'err 22023', 'un reintento fallido sin motivo se rechaza');
select lives_ok($$set role purge_job; select public.record_calendar_scrub_result(  -- 19
  '00000000-0000-0000-0000-0000000000a1', 'ev2', false, repeat('x', 500)); reset role$$, 'motivo largo se acepta');
select is((select length(metadata ->> 'motivo') from public.audit_log  -- 20
            where action = 'calendar_scrub_retry_failed' and metadata ->> 'event_id' = 'ev2'), 200,
  'el motivo se recorta a 200 caracteres');

-- Resolver
select is(pg_temp.como('purge_job',  -- 21
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev1', true)::text$$),
  '', 'resolver se registra');
select is(pg_temp.pendientes(),  -- 22
  '01|ev2|2|-;01|evz_sin_integ|1|f1;02|ev-b|1|-', 'una resuelta no vuelve a listarse');
select is(pg_temp.como('purge_job',  -- 23
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev1', true)::text$$),
  'err P0002', 'resolver de nuevo una ya resuelta falla (no duplica filas)');
select is((select count(*) from public.audit_log where action = 'calendar_scrub_resolved'), 1::bigint,  -- 24
  'una sola fila resolved para ev1');

-- Aislamiento por tenant: el mismo event_id de otro tenant no se ve afectado
select is(pg_temp.como('purge_job',  -- 25
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a2', 'ev1', true)::text$$),
  'err P0002', 'un evento de otro tenant (sin fallo propio) no se puede resolver');
select is(pg_temp.como('purge_job',  -- 26
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev-ajeno', true)::text$$),
  'err P0002', 'una accion que no es de limpieza no cuenta como pendiente');
select is(pg_temp.como('purge_job',  -- 27
  $$select public.record_calendar_scrub_result(null, 'ev1', true)::text$$), 'err 22023', 'tenant nulo se rechaza');

-- Un fallo nuevo posterior a la resolucion reabre la limpieza
insert into public.audit_log (tenant_id, actor_type, action, entity, metadata) values
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "ev1", "motivo": "otra_vez"}');
select is(pg_temp.pendientes(),  -- 28
  '01|ev1|1|f1;01|ev2|2|-;01|evz_sin_integ|1|f1;02|ev-b|1|-',
  'un fallo posterior a la resolucion reabre el evento (intentos desde cero)');

-- La cadena de hashes sigue integra para los dos tenants
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000a1'), null,  -- 29
  'la cadena del audit_log del tenant A sigue integra');
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000a2'), null,  -- 30
  'la cadena del audit_log del tenant B sigue integra');

select * from finish();
rollback;
