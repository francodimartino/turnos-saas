-- Purga de tenant (§2.2, §3.2, §5.4.1): list_tenants_due_for_purge, purge_tenant_data,
-- mark_tenant_purged y prune_inbound_messages.
--
-- purge_job no tiene grants sobre tablas: las funciones corren con los permisos del dueño. Las
-- llamadas "como purge_job" usan `set role` (alcanza para EXECUTE y para comprobar que no hace falta
-- ningún grant de tabla); lo que depende de session_user con una conexión real está en
-- tests/integracion/test_anonimizacion_purga.py. La rama de dominio (nombre de professionals,
-- eventos de Google Calendar) no vive en estas funciones de plataforma.
begin;
select plan(62);

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
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- Tenants: activo, suspendido, inactivo con período sin vencer (10 de 90 días) y dos inactivos vencidos
-- (100 días: 10 de atraso). deactivated_at se fija a mano: el trigger de transiciones solo corre al cambiar status.
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000d1', 'Activo', 'purga-activo', 'active', null),
  ('00000000-0000-0000-0000-0000000000d2', 'Suspendido', 'purga-suspendido', 'suspended', null),
  ('00000000-0000-0000-0000-0000000000d3', 'Inactivo reciente', 'purga-reciente', 'inactive', now() - interval '10 days'),
  ('00000000-0000-0000-0000-0000000000d4', 'Inactivo vencido', 'purga-vencido', 'inactive', now() - interval '100 days'),
  ('00000000-0000-0000-0000-0000000000d5', 'Inactivo vencido 2', 'purga-vencido-2', 'inactive', now() - interval '100 days');

-- ---------------------------------------------------------------------------
-- list_tenants_due_for_purge: los días de atraso se calculan en la base
-- ---------------------------------------------------------------------------
select is((select string_agg(tenant_id::text, ',' order by tenant_id) from public.list_tenants_due_for_purge()),  -- 1
  '00000000-0000-0000-0000-0000000000d4,00000000-0000-0000-0000-0000000000d5',
  'lista solo los inactive con el período vencido (no active, suspended ni inactive reciente)');
select is((select days_overdue from public.list_tenants_due_for_purge()  -- 2
            where tenant_id = '00000000-0000-0000-0000-0000000000d4'),
  10, 'días de atraso = 100 - 90 (período de platform_settings)');
select is((select pg_typeof(days_overdue)::text from public.list_tenants_due_for_purge() limit 1),  -- 3
  'integer', 'days_overdue es un entero');

update public.platform_settings set value = '120'::jsonb where key = 'tenant_deletion_grace_period_days';
select is((select count(*) from public.list_tenants_due_for_purge()), 0::bigint,  -- 4
  'con un período de 120 días ninguno de 100 días de baja venció');
select throws_ok($$update public.platform_settings set value = '"noventa"'::jsonb where key = 'tenant_deletion_grace_period_days'$$,  -- 5
  '23514', null, 'un valor no numérico ya no se puede guardar');
-- un valor menor a 30 que ya estuviera guardado (antes del trigger) se ignora: no vence ninguno
alter table public.platform_settings disable trigger platform_settings_validar_gracia;
update public.platform_settings set value = '10'::jsonb where key = 'tenant_deletion_grace_period_days';
alter table public.platform_settings enable trigger platform_settings_validar_gracia;
select is((select count(*) from public.list_tenants_due_for_purge()), 0::bigint,  -- 5b
  'un valor guardado menor a 30 días no se interpreta como gracia corta: no vence ninguno');
delete from public.platform_settings where key = 'tenant_deletion_grace_period_days';
select is((select count(*) from public.list_tenants_due_for_purge()), 0::bigint,  -- 6
  'sin la fila de configuración tampoco (nunca "0 días" implícito)');
insert into public.platform_settings (key, value) values ('tenant_deletion_grace_period_days', '90'::jsonb);
select is((select count(*) from public.list_tenants_due_for_purge()), 2::bigint,  -- 7
  'con 90 días vuelven a figurar los dos vencidos');

-- ---------------------------------------------------------------------------
-- purge_tenant_data: la base re-valida (rechaza active, suspended e inactive sin vencer)
-- ---------------------------------------------------------------------------
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, extra_data) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000d1', 'telegram', 'act-1', '+5491100000001', 'Activo 1', '{"k": 1}'),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000d2', 'telegram', 'sus-1', '+5491100000002', 'Suspendido 1', '{}'),
  ('00000000-0000-0000-0000-0000000000f3', '00000000-0000-0000-0000-0000000000d3', 'telegram', 'rec-1', '+5491100000003', 'Reciente 1', '{}');

select throws_ok($$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d1')$$,  -- 8
  '23514', 'purga rechazada: el tenant no está inactive ni en purga', 'rechaza un tenant active');
select throws_ok($$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d2')$$,  -- 9
  '23514', 'purga rechazada: el tenant no está inactive ni en purga', 'rechaza un tenant suspended');
select throws_ok($$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d3')$$,  -- 10
  '23514', 'purga rechazada: el período de gracia no venció', 'rechaza un tenant inactive con el período sin vencer');
select throws_ok($$select * from public.purge_tenant_data('00000000-0000-0000-0000-00000000dead')$$,  -- 11
  'P0002', 'tenant inexistente', 'rechaza un tenant inexistente');
select is((select count(*) from public.end_customers where deleted_at is not null  -- 12
            and tenant_id in ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000d2',
                              '00000000-0000-0000-0000-0000000000d3')), 0::bigint,
  'los rechazos no anonimizaron a nadie');
select ok(pg_temp.como('purge_job',  -- 13
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d1')$$)
  like 'err 23514%purga rechazada%', 'también rechaza cuando la llama purge_job');
select is((select count(*) from public.audit_log where action = 'tenant_purge_step'), 0::bigint,  -- 14
  'un rechazo no deja pasos en audit_log');

-- ---------------------------------------------------------------------------
-- purge_tenant_data sobre un tenant vencido (como purge_job, sin grants de tabla)
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000a9'), ('00000000-0000-0000-0000-0000000000aa');
insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b9', '00000000-0000-0000-0000-0000000000d4', '00000000-0000-0000-0000-0000000000a9', true),
  ('00000000-0000-0000-0000-0000000000ba', '00000000-0000-0000-0000-0000000000d4', '00000000-0000-0000-0000-0000000000aa', false),
  ('00000000-0000-0000-0000-0000000000bb', '00000000-0000-0000-0000-0000000000d4', null, false);

insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, extra_data) values
  ('00000000-0000-0000-0000-0000000000f4', '00000000-0000-0000-0000-0000000000d4', 'telegram', 'ven-1', '+5491100000004', 'Vencido 1', '{"k": 1}'),
  ('00000000-0000-0000-0000-0000000000f5', '00000000-0000-0000-0000-0000000000d4', 'whatsapp', 'ven-2', '+5491100000005', 'Vencido 2', '{}');
insert into public.end_customers (id, tenant_id, phone, name) values
  ('00000000-0000-0000-0000-0000000000f6', '00000000-0000-0000-0000-0000000000d4', '+5491100000006', 'Manual');
insert into public.bot_sessions (tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000d4', 'telegram', 'ven-1'),
  ('00000000-0000-0000-0000-0000000000d4', 'telegram', 'sin-cliente'),
  ('00000000-0000-0000-0000-0000000000d3', 'telegram', 'rec-1');
insert into public.notifications_log (tenant_id, end_customer_id, channel, status, last_error) values
  ('00000000-0000-0000-0000-0000000000d4', '00000000-0000-0000-0000-0000000000f4', 'telegram', 'failed', 'error del proveedor');

insert into public.tenant_integrations (id, tenant_id, integration_type, secret_id, config) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000d4', 'telegram_bot',
   vault.create_secret('secret-ven-1', 'purga-test-ven-1'), '{"webhook_key": "purga-wk-ven"}'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000d4', 'mercadopago',
   vault.create_secret('secret-ven-2', 'purga-test-ven-2'), '{}'),
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000d3', 'telegram_bot',
   vault.create_secret('secret-rec-1', 'purga-test-rec-1'), '{"webhook_key": "purga-wk-rec"}');

select is(pg_temp.como('purge_job',  -- 15
  $$select string_agg(p::text, ',' order by p::text) from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d4') p$$),
  '00000000-0000-0000-0000-0000000000a9,00000000-0000-0000-0000-0000000000aa',
  'purge_tenant_data (como purge_job) devuelve los auth_user_id de sus tenant_users');
select is((select count(*) from public.end_customers  -- 16
            where tenant_id = '00000000-0000-0000-0000-0000000000d4'
              and (phone is not null or name is not null or extra_data <> '{}'::jsonb or channel_user_id is not null
                   or deleted_at is null)), 0::bigint,
  'todos los end_customers del tenant quedaron anonimizados (incluida el alta manual)');
select is((select count(*) from public.end_customers  -- 17
            where tenant_id = '00000000-0000-0000-0000-0000000000d4' and channel is not null
              and channel_user_hash ~ '^[0-9a-f]{64}$'), 2::bigint,
  'los end_customers con canal conservan el hash para el recontacto');
select is((select count(*) from public.notifications_log  -- 18
            where tenant_id = '00000000-0000-0000-0000-0000000000d4' and last_error is not null), 0::bigint,
  'notifications_log.last_error en null');
select is((select count(*) from public.bot_sessions where tenant_id = '00000000-0000-0000-0000-0000000000d4'),  -- 19
  0::bigint, 'se borran las bot_sessions del tenant (también las de usuarios sin end_customer)');
select is((select count(*) from vault.secrets where name in ('purga-test-ven-1', 'purga-test-ven-2')),  -- 20
  0::bigint, 'los secrets de Vault de sus integraciones se eliminaron');
select is((select count(*) from public.tenant_integrations  -- 21
            where tenant_id = '00000000-0000-0000-0000-0000000000d4' and secret_id is not null), 0::bigint,
  'tenant_integrations ya no referencia ningún secret');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-0000000000d4'),  -- 22
  'purging', 'purge_tenant_data deja el tenant en purging (no purged: eso es mark_tenant_purged, tras eliminar las cuentas)');
select is((select count(*) from public.tenant_users  -- 23
            where tenant_id = '00000000-0000-0000-0000-0000000000d4' and auth_user_id is not null), 2::bigint,
  'las cuentas siguen vinculadas hasta que el job las elimine vía Auth Admin API');

-- los pasos quedan en audit_log, sin PII, con actor system
select is((select string_agg(distinct metadata ->> 'step', ',' order by metadata ->> 'step')  -- 24
             from public.audit_log where action = 'tenant_purge_step'),
  'anonymize_end_customers,begin_tenant_purge,collect_auth_users,delete_bot_sessions,delete_vault_secrets',
  'cada paso de la cascada deja audit_log con action = tenant_purge_step');
select is((select count(*) from public.audit_log  -- 25
            where action = 'tenant_purge_step'
              and (actor_type <> 'system' or tenant_id <> '00000000-0000-0000-0000-0000000000d4'
                   or entity <> 'tenants' or entity_id <> '00000000-0000-0000-0000-0000000000d4')), 0::bigint,
  'los pasos quedan como system, sobre el tenant purgado');
select is((select count(*) from public.audit_log where action = 'anonymize_end_customer'  -- 26
            and actor_type = 'system'), 3::bigint,
  'la anonimización anidada registra una fila system por end_customer');
select is((select metadata::text ~ '(5491100|Vencido|ven-1|secret-ven)' from public.audit_log  -- 27
            where action = 'tenant_purge_step' and metadata ->> 'step' = 'anonymize_end_customers'),
  false, 'los pasos no retienen valores originales');
select is((select (metadata ->> 'count')::integer from public.audit_log  -- 28
            where action = 'tenant_purge_step' and metadata ->> 'step' = 'anonymize_end_customers'),
  3, 'el paso registra cuántos end_customers anonimizó');

-- no toca a otros tenants
select is((select count(*) from public.end_customers  -- 29
            where tenant_id in ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000d2',
                                '00000000-0000-0000-0000-0000000000d3') and deleted_at is null), 3::bigint,
  'los end_customers de otros tenants quedan intactos');
select is((select count(*) from vault.secrets where name = 'purga-test-rec-1'), 1::bigint,  -- 30
  'los secrets de otro tenant quedan intactos');
select is((select count(*) from public.bot_sessions where tenant_id = '00000000-0000-0000-0000-0000000000d3'),  -- 31
  1::bigint, 'las sesiones de otro tenant quedan intactas');

-- idempotente: reintentar sobre algo ya purgado no falla ni cambia el resultado
select is(pg_temp.como('purge_job',  -- 32
  $$select string_agg(p::text, ',' order by p::text) from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d4') p$$),
  '00000000-0000-0000-0000-0000000000a9,00000000-0000-0000-0000-0000000000aa',
  'reintentar purge_tenant_data no falla y devuelve las mismas cuentas');
select is((select count(*) from public.audit_log where action = 'anonymize_end_customer'), 3::bigint,  -- 33
  'el reintento no vuelve a anonimizar (ni a auditar) a quien ya lo estaba');
select is((select count(*) from public.audit_log where action = 'tenant_purge_step'), 9::bigint,  -- 34
  'el reintento deja sus propios pasos en audit_log (begin + 4, luego 4)');

-- ---------------------------------------------------------------------------
-- mark_tenant_purged
-- ---------------------------------------------------------------------------
select throws_ok($$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d4')$$,  -- 35
  '23514', 'no se puede marcar purged: quedan cuentas vinculadas',
  'rechaza si quedan cuentas vinculadas en tenant_users');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-0000000000d4'),  -- 36
  'purging', 'y el tenant sigue purging');
select throws_ok($$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d1')$$,  -- 37
  '23514', 'purga rechazada: el tenant no está inactive ni en purga', 'rechaza un tenant active');
select throws_ok($$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d2')$$,  -- 38
  '23514', 'purga rechazada: el tenant no está inactive ni en purga', 'rechaza un tenant suspended');
select throws_ok($$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d3')$$,  -- 39
  '23514', 'purga rechazada: el período de gracia no venció', 'rechaza un inactive con el período sin vencer');
select throws_ok($$select public.mark_tenant_purged('00000000-0000-0000-0000-00000000dead')$$,  -- 40
  'P0002', 'tenant inexistente', 'rechaza un tenant inexistente');

-- el job elimina las cuentas vía Auth Admin API: la fila de tenant_users sobrevive con auth_user_id null
delete from auth.users where id in ('00000000-0000-0000-0000-0000000000a9', '00000000-0000-0000-0000-0000000000aa');
select is(pg_temp.como('purge_job',  -- 41
  $$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d4')$$),
  'purga-vencido-purged-' || to_char(timezone('America/Argentina/Buenos_Aires', now()), 'YYYYMMDD'),
  'sin cuentas vinculadas marca purged y devuelve el slug renombrado <slug>-purged-<fecha>');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-0000000000d4'),  -- 42
  'purged', 'el tenant queda purged');
select is((select slug from public.tenants where id = '00000000-0000-0000-0000-0000000000d4') ~ '^purga-vencido-purged-[0-9]{8}$',  -- 43
  true, 'el slug quedó con el formato <slug>-purged-AAAAMMDD');
select lives_ok(  -- 44
  $$insert into public.tenants (name, slug) values ('Vuelve a contratar', 'purga-vencido')$$,
  'el slug original quedó libre: el negocio se da de alta como una fila nueva');
select is(pg_temp.como('purge_job',  -- 45
  $$select public.mark_tenant_purged('00000000-0000-0000-0000-0000000000d4')$$),
  (select slug from public.tenants where id = '00000000-0000-0000-0000-0000000000d4'),
  'idempotente: reintentar sobre un tenant ya purged devuelve su slug sin cambiarlo');
select is((select count(*) from public.audit_log  -- 46
            where action = 'tenant_purge_step' and metadata ->> 'step' = 'mark_tenant_purged'
              and actor_type = 'system'), 1::bigint,
  'mark_tenant_purged deja un único paso en audit_log (el reintento no repite)');
select throws_ok($$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d4')$$,  -- 47
  '23514', 'purga rechazada: el tenant no está inactive ni en purga', 'un tenant purged no se vuelve a purgar');
select is((select count(*) from public.list_tenants_due_for_purge()  -- 48
            where tenant_id = '00000000-0000-0000-0000-0000000000d4'), 0::bigint,
  'un tenant purged ya no figura en la lista del job');

-- ---------------------------------------------------------------------------
-- prune_inbound_messages: solo filas de más de 7 días
-- ---------------------------------------------------------------------------
insert into public.inbound_messages (tenant_id, channel, external_message_id, received_at) values
  ('00000000-0000-0000-0000-0000000000d1', 'telegram', 'prune-viejo-8d', now() - interval '8 days'),
  ('00000000-0000-0000-0000-0000000000d1', 'telegram', 'prune-viejo-30d', now() - interval '30 days'),
  ('00000000-0000-0000-0000-0000000000d1', 'telegram', 'prune-6d', now() - interval '6 days'),
  ('00000000-0000-0000-0000-0000000000d1', 'telegram', 'prune-nuevo', now());
select ok(pg_temp.como('purge_job', $$select public.prune_inbound_messages()::text$$)::integer >= 2,  -- 49
  'prune_inbound_messages (como purge_job) devuelve la cantidad borrada');
select is((select string_agg(external_message_id, ',' order by external_message_id)  -- 50
             from public.inbound_messages where external_message_id like 'prune-%'),
  'prune-6d,prune-nuevo', 'borra solo las de más de 7 días');
select is(pg_temp.como('purge_job', $$select public.prune_inbound_messages()::text$$), '0',  -- 51
  'idempotente: la segunda corrida no borra nada');

-- ---------------------------------------------------------------------------
-- EXECUTE (§3.2): purge_job solo en sus funciones; nadie más en las de purga
-- ---------------------------------------------------------------------------
select is_empty(  -- 52
  $$select f from unnest(array['list_tenants_due_for_purge()', 'purge_tenant_data(uuid)', 'begin_tenant_purge(uuid)',
                               'mark_tenant_purged(uuid)', 'prune_inbound_messages()']) f
     where not has_function_privilege('purge_job', 'public.' || f, 'execute')$$,
  'purge_job ejecuta las cinco funciones de purga');
select is_empty(  -- 53
  $$select r, f from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                                  'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     cross join unnest(array['list_tenants_due_for_purge()', 'purge_tenant_data(uuid)', 'begin_tenant_purge(uuid)',
                             'mark_tenant_purged(uuid)', 'prune_inbound_messages()']) f
     where has_function_privilege(r, 'public.' || f, 'execute')$$,
  'ningún otro rol ejecuta las funciones de purga');
select is_empty(  -- 54
  $$select p.proname from pg_proc p
     where p.pronamespace = 'public'::regnamespace and p.prorettype <> 'trigger'::regtype
       and has_function_privilege('purge_job', p.oid, 'execute')
       and p.proname not in ('list_tenants_due_for_purge', 'purge_tenant_data', 'mark_tenant_purged', 'begin_tenant_purge',
                             'prune_inbound_messages', 'write_audit_log', 'get_integration_secret',
                             'calendar_scrub_targets', 'list_calendar_scrubs_pending',
                             'record_calendar_scrub_result', 'unlink_platform_admin_accounts',
                             'audit_chain_heads')$$,
  'purge_job solo tiene EXECUTE en sus funciones de §3.2 (ninguna otra función de public)');
select is_empty(  -- 55
  $$select c.relname from pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm', 'f')
       and has_table_privilege('purge_job', c.oid, 'select, insert, update, delete, truncate, references, trigger')$$,
  'purge_job no tiene grants sobre ninguna tabla de public');
select ok(pg_temp.como('purge_job',  -- 56
  $$select public.anonymize_end_customer('00000000-0000-0000-0000-0000000000f1')::text$$)
  like 'err 42501%permission denied%', 'purge_job no ejecuta anonymize_end_customer directamente (solo anidada)');
select ok(pg_temp.como('bot_service',  -- 57
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000000d5')$$)
  like 'err 42501%permission denied%', 'bot_service no puede purgar');
select ok(pg_temp.como('dashboard_service',  -- 58
  $$select public.prune_inbound_messages()::text$$)
  like 'err 42501%permission denied%', 'dashboard_service no puede ejecutar prune_inbound_messages');

-- funciones: existen una vez, SECURITY DEFINER y con search_path fijo
create temp table funciones_b (fn text primary key);
insert into funciones_b values ('anonymize_end_customer'), ('list_tenants_due_for_purge'),
  ('purge_tenant_data'), ('begin_tenant_purge'), ('mark_tenant_purged'), ('prune_inbound_messages');
select is_empty(  -- 59
  $$select f.fn from funciones_b f
     where (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = f.fn) <> 1$$,
  'las funciones públicas de la migración existen, una sola firma cada una');
select is_empty(  -- 60
  $$select p.proname from pg_proc p join funciones_b f on f.fn = p.proname
     where p.pronamespace = 'public'::regnamespace
       and (not p.prosecdef or not coalesce(p.proconfig, '{}') @> array['search_path=""'])$$,
  'son SECURITY DEFINER con search_path vacío');
select is((select count(*) from public.audit_log where action = 'tenant_purge_step'  -- 61
            and metadata::text ~ '(5491100|Vencido|ven-1|secret-ven|purga-test)'), 0::bigint,
  'ningún paso de la cascada retiene PII ni secretos');

select * from finish();
rollback;
