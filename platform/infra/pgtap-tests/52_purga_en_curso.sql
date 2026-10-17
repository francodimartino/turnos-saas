-- Estado 'purging' (§2.2): una purga en curso no se puede reactivar.
--
-- Carrera que cierra: el job anonimiza los datos (purge_tenant_data) y recién después elimina las cuentas
-- de Auth y marca purged, en transacciones distintas. Antes de 'purging' el tenant seguía 'inactive' en ese
-- intervalo y un platform_admin lo podía reactivar: quedaba 'active' con los datos anonimizados.
-- Acá: begin_tenant_purge, rechazo de la reactivación (y de cualquier otra salida de purging), reintento del
-- job sobre un purging, la gracia mínima forzada en la base y que resolve_inbound_tenant ignora un purging.
-- La espera real de lock entre dos conexiones se prueba en tests/integracion (hace falta más de una sesión).
begin;
select plan(30);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['platform_admin', 'purge_job', 'bot_service'] loop
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

-- E1: inactive vencido (100 días) -> la purga en curso. E2: inactive vencido, sin empezar.
-- E3: inactive reciente. E4: active. E5: suspended.
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-000000005201', 'E1', 'pc-e1', 'inactive', now() - interval '100 days'),
  ('00000000-0000-0000-0000-000000005202', 'E2', 'pc-e2', 'inactive', now() - interval '100 days'),
  ('00000000-0000-0000-0000-000000005203', 'E3', 'pc-e3', 'inactive', now() - interval '10 days'),
  ('00000000-0000-0000-0000-000000005204', 'E4', 'pc-e4', 'active', null),
  ('00000000-0000-0000-0000-000000005205', 'E5', 'pc-e5', 'suspended', null);
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) values
  ('00000000-0000-0000-0000-0000000052f1', '00000000-0000-0000-0000-000000005201', 'telegram', 'pc-1', '+5491100005201', 'Cliente E1');
insert into public.tenant_integrations (tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-000000005201', 'telegram_bot', '{"webhook_key": "pc-wk-e1"}');

-- ---------------------------------------------------------------------------
-- begin_tenant_purge: validaciones
-- ---------------------------------------------------------------------------
select throws_ok($$select public.begin_tenant_purge('00000000-0000-0000-0000-000000005204')$$,  -- 1
  '23514', 'purga rechazada: el tenant no está inactive', 'rechaza un tenant active');
select throws_ok($$select public.begin_tenant_purge('00000000-0000-0000-0000-000000005205')$$,  -- 2
  '23514', 'purga rechazada: el tenant no está inactive', 'rechaza un tenant suspended');
select throws_ok($$select public.begin_tenant_purge('00000000-0000-0000-0000-000000005203')$$,  -- 3
  '23514', 'purga rechazada: el período de gracia no venció', 'rechaza un inactive con la gracia sin vencer');
select throws_ok($$select public.begin_tenant_purge('00000000-0000-0000-0000-00000000dead')$$,  -- 4
  'P0002', 'tenant inexistente', 'rechaza un tenant inexistente');
select is((select count(*) from public.tenants where status = 'purging'), 0::bigint,  -- 5
  'los rechazos no dejaron ningún tenant en purging');
select ok(pg_temp.como('platform_admin',  -- 6
  $$select public.begin_tenant_purge('00000000-0000-0000-0000-000000005201')::text$$) like 'err 42501%permission denied%',
  'platform_admin no puede empezar una purga');
select ok(pg_temp.como('bot_service',  -- 7
  $$select public.begin_tenant_purge('00000000-0000-0000-0000-000000005201')::text$$) like 'err 42501%permission denied%',
  'bot_service no puede empezar una purga');

-- ---------------------------------------------------------------------------
-- begin_tenant_purge: inactive -> purging, idempotente
-- ---------------------------------------------------------------------------
select is(pg_temp.como('purge_job',  -- 8
  $$select 'ok' from (select public.begin_tenant_purge('00000000-0000-0000-0000-000000005201')) s$$), 'ok',
  'purge_job pasa un inactive vencido a purging');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-000000005201'),  -- 9
  'purging', 'el tenant quedó purging');
select ok((select deactivated_at is not null from public.tenants where id = '00000000-0000-0000-0000-000000005201'),  -- 10
  'purging conserva deactivated_at');
select is(pg_temp.como('purge_job',  -- 11
  $$select 'ok' from (select public.begin_tenant_purge('00000000-0000-0000-0000-000000005201')) s$$), 'ok',
  'es idempotente: repetirlo sobre un purging no falla');
select is((select count(*) from public.audit_log  -- 12
            where action = 'tenant_purge_step' and metadata ->> 'step' = 'begin_tenant_purge'
              and tenant_id = '00000000-0000-0000-0000-000000005201' and actor_type = 'system'), 1::bigint,
  'queda un único paso begin_tenant_purge en audit_log (el reintento no repite)');

-- ---------------------------------------------------------------------------
-- La carrera: con la purga en curso, la reactivación es rechazada
-- ---------------------------------------------------------------------------
-- La purga ya anonimizó los datos (como en producción, en una transacción anterior a la reactivación)...
select is(pg_temp.como('purge_job',  -- 13
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-000000005201')$$), '0',
  'purge_tenant_data acepta un tenant purging (sin cuentas en este escenario)');
select is((select count(*) from public.end_customers  -- 14
            where tenant_id = '00000000-0000-0000-0000-000000005201' and name is null and phone is null
              and deleted_at is not null), 1::bigint,
  'los datos del tenant quedaron anonimizados');

-- ...y recién ahora un platform_admin intenta reactivar el tenant.
select ok(pg_temp.como('platform_admin',  -- 15
  $$update public.tenants set status = 'active' where id = '00000000-0000-0000-0000-000000005201' returning 'ok'$$)
  like 'err 23514%', 'reactivar (purging -> active) es rechazado con check_violation');
select throws_ok(  -- 16
  $$update public.tenants set status = 'active' where id = '00000000-0000-0000-0000-000000005201'$$,
  '23514', null, 'también el rechazo directo del trigger, sin pasar por roles');
select throws_ok(  -- 17
  $$update public.tenants set status = 'inactive' where id = '00000000-0000-0000-0000-000000005201'$$,
  '23514', null, 'purging -> inactive es rechazado');
select throws_ok(  -- 18
  $$update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-000000005201'$$,
  '23514', null, 'purging -> suspended es rechazado');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-000000005201'),  -- 19
  'purging', 'el tenant sigue purging: nunca queda active con los datos anonimizados');
select is((select count(*) from public.tenants  -- 20
            where id = '00000000-0000-0000-0000-000000005201' and status = 'active'), 0::bigint,
  'no existe ningún tenant active con los datos de la purga anonimizados');

-- inactive -> purged directo ya no existe: hay que pasar por begin_tenant_purge
select throws_ok(  -- 21
  $$update public.tenants set status = 'purged' where id = '00000000-0000-0000-0000-000000005202'$$,
  '23514', null, 'inactive -> purged directo es rechazado (hay que pasar por purging)');

-- ---------------------------------------------------------------------------
-- Reintento del job y listado
-- ---------------------------------------------------------------------------
select is((select string_agg(tenant_id::text, ',' order by tenant_id) from public.list_tenants_due_for_purge()),  -- 22
  '00000000-0000-0000-0000-000000005201,00000000-0000-0000-0000-000000005202',
  'la lista del job incluye el purging (a retomar) y el inactive vencido, y no el reciente');
update public.platform_settings set value = '365'::jsonb where key = 'tenant_deletion_grace_period_days';
select is((select string_agg(tenant_id::text, ',' order by tenant_id) from public.list_tenants_due_for_purge()),  -- 23
  '00000000-0000-0000-0000-000000005201',
  'si la gracia se alarga después, el purging igual se retoma y el inactive deja de estar vencido');
update public.platform_settings set value = '90'::jsonb where key = 'tenant_deletion_grace_period_days';

-- mark_tenant_purged parte de purging
select is(pg_temp.como('purge_job',  -- 24
  $$select public.mark_tenant_purged('00000000-0000-0000-0000-000000005201')$$),
  'pc-e1-purged-' || to_char(timezone('America/Argentina/Buenos_Aires', now()), 'YYYYMMDD'),
  'mark_tenant_purged parte de purging y deja purged');
select is((select status from public.tenants where id = '00000000-0000-0000-0000-000000005201'),  -- 25
  'purged', 'el tenant quedó purged');

-- ---------------------------------------------------------------------------
-- Un purging no tiene acceso
-- ---------------------------------------------------------------------------
update public.tenants set status = 'purging' where id = '00000000-0000-0000-0000-000000005202';
select is_empty(  -- 26
  $$select * from public.resolve_inbound_tenant('telegram', 'pc-wk-e1')$$,
  'resolve_inbound_tenant no resuelve un tenant purging/purged');
insert into public.tenant_integrations (tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-000000005202', 'telegram_bot', '{"webhook_key": "pc-wk-e2"}');
select is_empty(  -- 27
  $$select * from public.resolve_inbound_tenant('telegram', 'pc-wk-e2')$$,
  'resolve_inbound_tenant no resuelve un tenant purging');

-- ---------------------------------------------------------------------------
-- Gracia mínima de 30 días forzada en la base
-- ---------------------------------------------------------------------------
select throws_ok(  -- 28
  $$update public.platform_settings set value = '29'::jsonb where key = 'tenant_deletion_grace_period_days'$$,
  '23514', null, 'la base rechaza una gracia de 29 días');
select throws_ok(  -- 29
  $$insert into public.platform_settings (key, value) values ('tenant_deletion_grace_period_days', '1'::jsonb)
    on conflict (key) do update set value = excluded.value$$,
  '23514', null, 'tampoco por el upsert del panel');
select lives_ok(  -- 30
  $$update public.platform_settings set value = '30'::jsonb where key = 'tenant_deletion_grace_period_days'$$,
  'exactamente 30 días es válido');

select * from finish();
rollback;
