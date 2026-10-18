-- notification_outbox y sus funciones: enqueue_notification, complete_notification_outbox,
-- claim_notification_outbox, prune_notification_outbox (§6.4).
-- Aislamiento por tenant, grants (solo funciones, ningún privilegio de tabla), encolado dentro de la transacción
-- del llamador, reclamo sin repetir filas y borrado en anonimización y purga. El reclamo con dos conexiones
-- concurrentes está en tests/integracion/test_notification_outbox.py (pgTAP corre en una sola sesión).
begin;
select plan(41);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Ejecuta una consulta como un rol, con el contexto de tenant dado ('' = sin contexto).
create function pg_temp.como(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
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

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000059a1', 'A', 'outbox-a', 'active', null),
  ('00000000-0000-0000-0000-0000000059a2', 'B', 'outbox-b', 'active', null),
  ('00000000-0000-0000-0000-0000000059a3', 'Vencido', 'outbox-vencido', 'inactive', now() - interval '100 days');
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) values
  ('00000000-0000-0000-0000-0000000059c1', '00000000-0000-0000-0000-0000000059a1', 'telegram', 'o-c1', '+5491100000001', 'Uno'),
  ('00000000-0000-0000-0000-0000000059c2', '00000000-0000-0000-0000-0000000059a2', 'telegram', 'o-c2', '+5491100000002', 'Dos'),
  ('00000000-0000-0000-0000-0000000059c3', '00000000-0000-0000-0000-0000000059a1', 'telegram', 'o-c3', '+5491100000003', 'Tres'),
  ('00000000-0000-0000-0000-0000000059c4', '00000000-0000-0000-0000-0000000059a3', 'telegram', 'o-c4', '+5491100000004', 'Cuatro');

-- Estructura y grants
select has_table('public', 'notification_outbox', 'existe notification_outbox');  -- 1
select ok((select relrowsecurity from pg_class where oid = 'public.notification_outbox'::regclass),  -- 2
  'notification_outbox tiene RLS activada');
select ok(not exists (  -- 3
  select 1 from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                             'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
   where has_any_column_privilege(r, 'public.notification_outbox', 'SELECT,INSERT,UPDATE,REFERENCES')
      or has_table_privilege(r, 'public.notification_outbox', 'DELETE,TRUNCATE,TRIGGER')),
  'ningun rol de aplicacion tiene privilegios directos sobre la tabla');
select ok(  -- 4
  has_function_privilege('bot_service', 'public.enqueue_notification(uuid, text, text, jsonb, integer)', 'execute')
  and has_function_privilege('dashboard_service', 'public.enqueue_notification(uuid, text, text, jsonb, integer)', 'execute')
  and has_function_privilege('scheduler_job', 'public.enqueue_notification(uuid, text, text, jsonb, integer)', 'execute')
  and not has_function_privilege('purge_job', 'public.enqueue_notification(uuid, text, text, jsonb, integer)', 'execute')
  and not has_function_privilege('platform_admin', 'public.enqueue_notification(uuid, text, text, jsonb, integer)', 'execute'),
  'enqueue_notification: bot, dashboard y job de vencimiento');
select ok(  -- 5
  has_function_privilege('scheduler_job', 'public.claim_notification_outbox(integer, integer, integer)', 'execute')
  and not has_function_privilege('bot_service', 'public.claim_notification_outbox(integer, integer, integer)', 'execute')
  and not has_function_privilege('dashboard_service', 'public.claim_notification_outbox(integer, integer, integer)', 'execute')
  and has_function_privilege('scheduler_job', 'public.prune_notification_outbox(integer)', 'execute')
  and not has_function_privilege('bot_service', 'public.prune_notification_outbox(integer)', 'execute'),
  'claim y prune: solo scheduler_job');

-- enqueue_notification
select is(pg_temp.como('bot_service', '',  -- 6
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'telegram', 't')::text$$),
  'err 42501', 'sin contexto de tenant lanza insufficient_privilege');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 7
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c2', 'telegram', 't')::text$$),
  'err 42501', 'un cliente de otro tenant se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 8
  $$select public.enqueue_notification('00000000-0000-0000-0000-00000000dead', 'telegram', 't')::text$$),
  'err 42501', 'un cliente inexistente da el mismo error');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 9
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'telegram', 'turno_confirmado',
                                       '{"booking_id": "b1"}'::jsonb, 0)::text$$) ~ '^[0-9a-f-]{36}$',
  'bot_service encola y recibe el id');
select ok(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000059a1',  -- 10
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'telegram', 'turno_cancelado')::text$$)
  ~ '^[0-9a-f-]{36}$', 'dashboard_service encola');
select is((select status || ':' || attempts || ':' || (params ->> 'booking_id')  -- 11
             from public.notification_outbox where template = 'turno_confirmado'),
  'pending:0:b1', 'la fila nace pending, sin intentos y con sus params');
select ok((select next_attempt_at > now() + interval '4 minutes'  -- 12
             from public.notification_outbox where template = 'turno_cancelado'),
  'por defecto el primer intento queda a 5 minutos (ventana del envio inmediato)');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 13
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'telegram', 't', '[]'::jsonb)::text$$),
  'err 23514', 'params debe ser un objeto');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 14
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'sms', 't')::text$$),
  'err 23514', 'canal fuera de telegram/whatsapp se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 15
  $$select public.enqueue_notification('00000000-0000-0000-0000-0000000059c1', 'telegram', 't', '{}'::jsonb, -1)::text$$),
  'err 22023', 'demora negativa se rechaza');

-- trigger de tenant_id: ignora el valor que mande quien escribe
insert into public.notification_outbox (tenant_id, end_customer_id, channel, template)
  values ('00000000-0000-0000-0000-0000000059a2', '00000000-0000-0000-0000-0000000059c1', 'telegram', 'forzado');
select is((select tenant_id from public.notification_outbox where template = 'forzado'),  -- 16
  '00000000-0000-0000-0000-0000000059a1'::uuid, 'el trigger copia tenant_id desde end_customers');
delete from public.notification_outbox where template = 'forzado';

-- aislamiento: con SELECT concedido a un rol de prueba (se revierte con el rollback), RLS acota por tenant
grant select on public.notification_outbox to dashboard_service;
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000059a1',  -- 17
  $$select count(*)::text from public.notification_outbox$$), '2', 'el tenant A ve sus 2 filas');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000059a2',  -- 18
  $$select count(*)::text from public.notification_outbox$$), '0', 'el tenant B no ve las de A');
select is(pg_temp.como('dashboard_service', '',  -- 19
  $$select count(*)::text from public.notification_outbox$$), '0', 'sin contexto no se ve ninguna');
revoke select on public.notification_outbox from dashboard_service;

-- complete_notification_outbox
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a2',  -- 20
  format($$select public.complete_notification_outbox(%L, 'sent')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'false', 'otro tenant no puede cerrar la fila');
select is(pg_temp.como('bot_service', '',  -- 21
  format($$select public.complete_notification_outbox(%L, 'sent')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'err 42501', 'sin contexto de tenant lanza insufficient_privilege');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 22
  format($$select public.complete_notification_outbox(%L, 'enviado')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'err 22023', 'estado invalido se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 23
  format($$select public.complete_notification_outbox(%L, 'pending')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'err 22023', 'reprogramar sin p_retry_in_seconds se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 24
  format($$select public.complete_notification_outbox(%L, 'pending', 'timeout', 120)::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'true', 'reprogramar una fila pending');
select ok((select next_attempt_at > now() + interval '100 seconds' and status = 'pending' and last_error = 'timeout'  -- 25
             from public.notification_outbox where template = 'turno_confirmado'),
  'la reprogramacion mueve next_attempt_at y guarda el error');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000059a1',  -- 26
  format($$select public.complete_notification_outbox(%L, 'sent')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'true', 'cierra la fila como sent');
select is((select (status = 'sent' and processed_at is not null)::text  -- 27
             from public.notification_outbox where template = 'turno_confirmado'),
  'true', 'sent deja processed_at');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 28
  format($$select public.complete_notification_outbox(%L, 'failed', 'x')::text$$,
         (select id from public.notification_outbox where template = 'turno_confirmado'))),
  'false', 'una fila en estado final no se reabre ni se pisa');

-- claim_notification_outbox
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000059a1',  -- 29
  $$select count(*)::text from public.claim_notification_outbox()$$), 'err 42501',
  'bot_service no puede reclamar');
select is(pg_temp.como('scheduler_job', '', $$select count(*)::text from public.claim_notification_outbox(0)$$),  -- 30
  'err 22023', 'limite invalido se rechaza');
-- dos filas vencidas + una a futuro (la 'turno_cancelado' del tenant A sigue a +5 minutos)
select public.enqueue_notification('00000000-0000-0000-0000-0000000059c3', 'telegram', 'v1', '{}'::jsonb, 0)
  from (select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000059a1', true)) s;
select public.enqueue_notification('00000000-0000-0000-0000-0000000059c3', 'telegram', 'v2', '{}'::jsonb, 0)
  from (select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000059a1', true)) s;
select is(pg_temp.como('scheduler_job', '',  -- 31
  $$select string_agg(template, ',' order by template) from public.claim_notification_outbox(10)$$),
  'v1,v2', 'el reclamo devuelve solo las vencidas (no la de dentro de 5 minutos)');
select is((select string_agg(template || ':' || attempts, ',' order by template) from public.notification_outbox  -- 32
            where template in ('v1', 'v2', 'turno_cancelado')),
  'turno_cancelado:0,v1:1,v2:1', 'reclamar suma un intento');
select ok((select min(next_attempt_at) > now() + interval '4 minutes' from public.notification_outbox  -- 33
            where template in ('v1', 'v2')), 'las reclamadas quedan arrendadas (next_attempt_at en el futuro)');
select is(pg_temp.como('scheduler_job', '',  -- 34
  $$select count(*)::text from public.claim_notification_outbox(10)$$), '0',
  'un segundo reclamo no repite las filas arrendadas');
-- agotadas: pasan a failed sin devolverse
update public.notification_outbox set attempts = 5, next_attempt_at = now() - interval '1 minute'
 where template = 'v1';
select is(pg_temp.como('scheduler_job', '',  -- 35
  $$select count(*)::text from public.claim_notification_outbox(10, 300, 5)$$), '0',
  'una fila con los reclamos agotados no se devuelve');
select is((select status from public.notification_outbox where template = 'v1'),  -- 36
  'failed', 'y queda failed (dead-letter)');

-- prune
update public.notification_outbox set processed_at = now() - interval '40 days' where template = 'v1';
update public.notification_outbox set status = 'sent', processed_at = now() - interval '2 days' where template = 'v2';
select is(pg_temp.como('scheduler_job', '', $$select public.prune_notification_outbox(30)::text$$), '1',  -- 37
  'prune borra solo lo final con mas de 30 dias');
select is((select count(*) from public.notification_outbox where template in ('v2', 'turno_cancelado')),  -- 38
  2::bigint, 'prune conserva lo reciente y lo pending');

-- anonimizacion: borra las filas del cliente
select is((select count(*) from public.notification_outbox where end_customer_id = '00000000-0000-0000-0000-0000000059c1'),  -- 39
  2::bigint, 'antes de anonimizar el cliente tiene filas en el outbox');
select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000059a1', true);
select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000059c1', 'staff', '00000000-0000-0000-0000-00000000aaaa');
select is((select count(*) from public.notification_outbox where end_customer_id = '00000000-0000-0000-0000-0000000059c1')  -- 40
          + (select count(*) from public.notification_outbox where end_customer_id = '00000000-0000-0000-0000-0000000059c3'),
  1::bigint, 'anonimizar borra las filas del cliente y deja las de otros');

-- purga: borra todas las filas del tenant, incluso las de un cliente ya anonimizado antes
update public.end_customers set deleted_at = now() where id = '00000000-0000-0000-0000-0000000059c4';
insert into public.notification_outbox (tenant_id, end_customer_id, channel, template)
  values ('00000000-0000-0000-0000-0000000059a3', '00000000-0000-0000-0000-0000000059c4', 'telegram', 'residual');
select pg_temp.como('purge_job', '',
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000059a3')$$);
select is((select count(*) from public.notification_outbox where tenant_id = '00000000-0000-0000-0000-0000000059a3'),  -- 41
  0::bigint, 'purge_tenant_data borra las filas del outbox del tenant');

select * from finish();
rollback;
