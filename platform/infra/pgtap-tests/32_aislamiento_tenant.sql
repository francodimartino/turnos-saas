-- Aislamiento por tenant y grants efectivos de bot_service, dashboard_service,
-- platform_admin_readonly, purge_job y scheduler_job (§3.1.2, §3.2, §3.3).
--
-- Cada caso corre con el rol real: `set role <rol>` (current_user es el rol de login) y el contexto de tenant seteado como lo hace
-- platform_tenancy. Los casos se ejecutan dentro de helpers temporales y sus resultados se
-- guardan en una tabla; las aserciones de pgTAP corren después, ya como el usuario de prueba.
--
--   tenants de prueba: A = ...a1, B = ...a2. En los SQL de los casos, {A} y {B} son esos ids.
begin;

-- El rol de migraciones (postgres) no es superusuario en Supabase: no puede usar
-- SET SESSION AUTHORIZATION. Para actuar como cada rol de login usa SET ROLE, y para eso se
-- le otorga membresía con la opción SET dentro de esta transacción (se revierte con el rollback).
-- El caller con session_user real (conexión autenticada como el rol) lo cubren los tests de
-- integración de Python.
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

create function pg_temp.sustituir(p_sql text) returns text language sql as $$
  select replace(replace(p_sql, '{A}', '00000000-0000-0000-0000-0000000000a1'),
                 '{B}', '00000000-0000-0000-0000-0000000000a2')
$$;

-- Cantidad de filas que ve `p_consulta` (select ... from ... where ...) como p_rol con contexto
-- de tenant p_tenant ('A', 'B' o null = sin contexto). Si falla: 'err <sqlstate> <mensaje>'.
create function pg_temp.contar(p_rol text, p_tenant text, p_consulta text) returns text
language plpgsql as $$
declare
  v_sql text := pg_temp.sustituir(p_consulta);
  v_ctx text := coalesce(pg_temp.sustituir(case p_tenant when 'A' then '{A}' when 'B' then '{B}' end), '');
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute 'select count(*) from (' || v_sql || ') q' into v_n;
    reset role;
    return v_n::text;
  exception when others then
    reset role;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- Ejecuta una sentencia (insert/update/delete) como p_rol y devuelve 'ok <filas afectadas>' o
-- 'err <sqlstate> <mensaje>'. Los efectos se revierten siempre (subtransacción abortada a
-- propósito), así los casos son independientes entre sí.
create function pg_temp.ejecutar(p_rol text, p_tenant text, p_sql text) returns text
language plpgsql as $$
declare
  v_sql text := pg_temp.sustituir(p_sql);
  v_ctx text := coalesce(pg_temp.sustituir(case p_tenant when 'A' then '{A}' when 'B' then '{B}' end), '');
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute v_sql;
    get diagnostics v_n = row_count;
    raise exception 'revertir' using errcode = 'ZZ001';
  exception when others then
    reset role;
    if sqlstate = 'ZZ001' then
      return 'ok ' || v_n;
    end if;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- ---------------------------------------------------------------------------
-- Datos de prueba (como superusuario): dos tenants con filas en todas las tablas
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'iso-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'iso-b');
insert into public.tenant_users (id, tenant_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', true),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', true),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a1', false);
insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 'view_bookings'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2', 'view_bookings');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');
insert into public.tenant_integrations (tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-0000000000a1', 'telegram_bot', '{"webhook_key": "iso-k-a"}'),
  ('00000000-0000-0000-0000-0000000000a2', 'telegram_bot', '{"webhook_key": "iso-k-b"}');
insert into public.audit_log (tenant_id, actor_type, action) values
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'accion_a'),
  ('00000000-0000-0000-0000-0000000000a1', 'platform_admin', 'impersonate_view'),
  ('00000000-0000-0000-0000-0000000000a2', 'staff', 'accion_b'),
  (null, 'platform_admin', 'editar_platform_settings');
insert into public.inbound_messages (tenant_id, channel, external_message_id) values
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'm1'),
  ('00000000-0000-0000-0000-0000000000a2', 'telegram', 'm1');
insert into public.bot_sessions (tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');
insert into public.notifications_log (tenant_id, end_customer_id, channel) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000e1', 'telegram'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000e2', 'telegram');
insert into public.payments (tenant_id, payable_type, amount, status) values
  ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 10, 'pending'),
  ('00000000-0000-0000-0000-0000000000a2', 'booking_deposit', 10, 'pending');

create temp table casos (n serial, descr text, esperado text, real text);

-- ---------------------------------------------------------------------------
-- bot_service con contexto del tenant A
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real) values
  ('bot_service ve solo su fila de tenants', '1',
    pg_temp.contar('bot_service', 'A', 'select 1 from public.tenants')),
  ('bot_service no ve el tenant B', '0',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.tenants where id = '{B}'$q$)),
  ('bot_service no actualiza tenants', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.tenants set name = 'x' where id = '{A}'$q$)),
  ('bot_service sin contexto de tenant no ve nada (GUC vacío)', '0',
    pg_temp.contar('bot_service', null, 'select 1 from public.end_customers'));

insert into casos (descr, esperado, real) values
  ('bot_service ve end_customers propios', '1',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.end_customers where tenant_id = '{A}'$q$)),
  ('bot_service no ve end_customers de B', '0',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.end_customers where tenant_id = '{B}'$q$)),
  ('bot_service no inserta end_customers en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.end_customers (tenant_id, channel, channel_user_id) values ('{B}', 'telegram', 'x1')$q$)),
  ('bot_service no actualiza end_customers de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.end_customers set name = 'x' where tenant_id = '{B}'$q$)),
  ('bot_service no puede mover un end_customer propio a B', 'err 42501 new row violates%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.end_customers set tenant_id = '{B}' where tenant_id = '{A}'$q$)),
  ('bot_service inserta end_customers propios', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.end_customers (tenant_id, channel, channel_user_id) values ('{A}', 'telegram', 'nuevo')$q$)),
  ('bot_service actualiza end_customers propios', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.end_customers set name = 'n' where tenant_id = '{A}'$q$)),
  ('bot_service no borra end_customers', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$delete from public.end_customers where tenant_id = '{A}'$q$));

insert into casos (descr, esperado, real) values
  ('bot_service ve notifications_log propio', '1',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.notifications_log where tenant_id = '{A}'$q$)),
  ('bot_service no ve notifications_log de B', '0',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.notifications_log where tenant_id = '{B}'$q$)),
  ('bot_service no inserta notifications_log para un end_customer de B', 'err 23503%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.notifications_log (tenant_id, end_customer_id, channel)
         values ('{B}', '00000000-0000-0000-0000-0000000000e2', 'telegram')$q$)),
  ('bot_service no actualiza notifications_log de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.notifications_log set retry_count = 9 where tenant_id = '{B}'$q$)),
  ('bot_service no borra notifications_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', 'delete from public.notifications_log'));

insert into casos (descr, esperado, real) values
  ('bot_service ve payments propios', '1',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.payments where tenant_id = '{A}'$q$)),
  ('bot_service no ve payments de B', '0',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.payments where tenant_id = '{B}'$q$)),
  ('bot_service no inserta payments en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.payments (tenant_id, payable_type, amount, status)
         values ('{B}', 'booking_deposit', 5, 'pending')$q$)),
  ('bot_service inserta payments propios', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.payments (tenant_id, payable_type, amount, status)
         values ('{A}', 'booking_deposit', 5, 'pending')$q$)),
  ('bot_service no actualiza payments de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.payments set status = 'approved' where tenant_id = '{B}'$q$)),
  ('bot_service no borra payments', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', 'delete from public.payments'));

insert into casos (descr, esperado, real) values
  ('bot_service ve bot_sessions propias', '1',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.bot_sessions where tenant_id = '{A}'$q$)),
  ('bot_service no ve bot_sessions de B', '0',
    pg_temp.contar('bot_service', 'A', $q$select 1 from public.bot_sessions where tenant_id = '{B}'$q$)),
  ('bot_service no inserta bot_sessions en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.bot_sessions (tenant_id, channel, channel_user_id) values ('{B}', 'telegram', 'zz')$q$)),
  ('bot_service no actualiza bot_sessions de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.bot_sessions set state = '{"a": 1}' where tenant_id = '{B}'$q$)),
  ('bot_service no borra bot_sessions de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$delete from public.bot_sessions where tenant_id = '{B}'$q$)),
  ('bot_service borra bot_sessions propias (estado efímero)', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A', $q$delete from public.bot_sessions where tenant_id = '{A}'$q$));

insert into casos (descr, esperado, real) values
  ('bot_service no inserta inbound_messages en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.inbound_messages (tenant_id, channel, external_message_id) values ('{B}', 'telegram', 'm9')$q$)),
  ('bot_service inserta inbound_messages propios', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.inbound_messages (tenant_id, channel, external_message_id) values ('{A}', 'telegram', 'm9')$q$)),
  ('bot_service deduplica reentregas con ON CONFLICT DO NOTHING', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.inbound_messages (tenant_id, channel, external_message_id)
         values ('{A}', 'telegram', 'm1') on conflict do nothing$q$)),
  ('bot_service no lee inbound_messages', 'err 42501 permission denied%',
    pg_temp.contar('bot_service', 'A', 'select 1 from public.inbound_messages')),
  ('bot_service no actualiza inbound_messages', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.inbound_messages set channel = 'whatsapp'$q$)),
  ('bot_service no borra inbound_messages', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', 'delete from public.inbound_messages'));

-- bot_service sin acceso a las demás tablas de plataforma
insert into casos (descr, esperado, real)
select 'bot_service sin SELECT en ' || t, 'err 42501 permission denied%',
       pg_temp.contar('bot_service', 'A', format('select * from public.%I', t))
  from unnest(array['tenant_users', 'tenant_user_permissions', 'permissions', 'platform_settings',
                    'platform_admins', 'tenant_integrations', 'audit_log']) t;
insert into casos (descr, esperado, real) values
  ('bot_service no lee secret_id de tenant_integrations', 'err 42501 permission denied%',
    pg_temp.contar('bot_service', 'A', 'select secret_id from public.tenant_integrations')),
  ('bot_service no escribe audit_log directamente', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$insert into public.audit_log (actor_type, action) values ('bot', 'x')$q$));

-- ---------------------------------------------------------------------------
-- dashboard_service con contexto del tenant A
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real) values
  ('dashboard_service ve solo su fila de tenants', '1',
    pg_temp.contar('dashboard_service', 'A', 'select 1 from public.tenants')),
  ('dashboard_service no ve el tenant B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.tenants where id = '{B}'$q$)),
  ('dashboard_service actualiza tenants.settings propio', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$update public.tenants set settings = '{"direccion": "Calle 1"}' where id = '{A}'$q$)),
  ('dashboard_service no actualiza tenants.settings de B', 'ok 0',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$update public.tenants set settings = '{"direccion": "hackeado"}' where id = '{B}'$q$)),
  ('dashboard_service no puede UPDATE tenants set status (propio)', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenants set status = 'suspended' where id = '{A}'$q$)),
  ('dashboard_service no puede UPDATE tenants set paid_until', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenants set paid_until = now()::date where id = '{A}'$q$)),
  ('dashboard_service no puede UPDATE tenants set name', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenants set name = 'x' where id = '{A}'$q$)),
  ('dashboard_service no inserta tenants', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$insert into public.tenants (name, slug) values ('n', 'n')$q$)),
  ('dashboard_service no borra tenants', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.tenants')),
  ('dashboard_service sin contexto de tenant no ve nada (GUC vacío)', '0',
    pg_temp.contar('dashboard_service', null, 'select 1 from public.tenant_users'));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve tenant_users propios', '2',
    pg_temp.contar('dashboard_service', 'A', 'select 1 from public.tenant_users')),
  ('dashboard_service no ve tenant_users de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.tenant_users where tenant_id = '{B}'$q$)),
  ('dashboard_service no inserta tenant_users en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.tenant_users (tenant_id) values ('{B}')$q$)),
  ('dashboard_service inserta tenant_users propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.tenant_users (tenant_id) values ('{A}')$q$)),
  ('dashboard_service no actualiza tenant_users de B', 'ok 0',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenant_users set is_active = false where tenant_id = '{B}'$q$)),
  ('dashboard_service no puede mover un tenant_user propio a B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$update public.tenant_users set tenant_id = '{B}' where id = '00000000-0000-0000-0000-0000000000b3'$q$)),
  ('dashboard_service no borra tenant_users', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.tenant_users'));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve tenant_user_permissions propios', '1',
    pg_temp.contar('dashboard_service', 'A', 'select 1 from public.tenant_user_permissions')),
  ('dashboard_service no ve tenant_user_permissions de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.tenant_user_permissions where tenant_id = '{B}'$q$)),
  ('dashboard_service no otorga permisos a un tenant_user de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
         values ('{B}', '00000000-0000-0000-0000-0000000000b2', 'edit_prices')$q$)),
  ('dashboard_service otorga permisos a un tenant_user propio', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
         values ('{A}', '00000000-0000-0000-0000-0000000000b3', 'edit_prices')$q$)),
  ('dashboard_service no revoca permisos de B', 'ok 0',
    pg_temp.ejecutar('dashboard_service', 'A', $q$delete from public.tenant_user_permissions where tenant_id = '{B}'$q$)),
  ('dashboard_service revoca permisos propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A', $q$delete from public.tenant_user_permissions where tenant_id = '{A}'$q$)),
  ('dashboard_service no actualiza tenant_user_permissions', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenant_user_permissions set permission_key = 'view_reports'$q$));

insert into casos (descr, esperado, real) values
  ('dashboard_service lee el catálogo permissions', '1',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.permissions where key = 'view_bookings'$q$)),
  ('dashboard_service no escribe permissions', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$insert into public.permissions (key) values ('hackeo')$q$));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve end_customers propios', '1',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.end_customers where tenant_id = '{A}'$q$)),
  ('dashboard_service no ve end_customers de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.end_customers where tenant_id = '{B}'$q$)),
  ('dashboard_service no inserta end_customers en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.end_customers (tenant_id, channel, channel_user_id) values ('{B}', 'telegram', 'x1')$q$)),
  ('dashboard_service no actualiza end_customers de B', 'ok 0',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.end_customers set name = 'x' where tenant_id = '{B}'$q$)),
  ('dashboard_service no borra end_customers (borrar = anonymize_end_customer)', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.end_customers'));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve tenant_integrations propias (columnas sin secretos)', '1',
    pg_temp.contar('dashboard_service', 'A',
      'select id, tenant_id, integration_type, professional_id, created_at from public.tenant_integrations')),
  ('dashboard_service no ve tenant_integrations de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select id from public.tenant_integrations where tenant_id = '{B}'$q$)),
  ('dashboard_service no lee tenant_integrations.secret_id', 'err 42501 permission denied%',
    pg_temp.contar('dashboard_service', 'A', 'select secret_id from public.tenant_integrations')),
  ('dashboard_service no lee tenant_integrations.config', 'err 42501 permission denied%',
    pg_temp.contar('dashboard_service', 'A', 'select config from public.tenant_integrations')),
  ('dashboard_service no puede hacer select * de tenant_integrations', 'err 42501 permission denied%',
    pg_temp.contar('dashboard_service', 'A', 'select * from public.tenant_integrations')),
  ('dashboard_service no filtra por config (no es un oráculo de secretos)', 'err 42501 permission denied%',
    pg_temp.contar('dashboard_service', 'A', $q$select id from public.tenant_integrations where config ->> 'webhook_key' = 'iso-k-b'$q$)),
  ('dashboard_service no inserta tenant_integrations', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.tenant_integrations (tenant_id, integration_type) values ('{A}', 'mercadopago')$q$)),
  ('dashboard_service no actualiza tenant_integrations', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.tenant_integrations set integration_type = 'mercadopago'$q$)),
  ('dashboard_service no borra tenant_integrations', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.tenant_integrations'));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve audit_log de su tenant sin acciones de platform_admin', '1',
    pg_temp.contar('dashboard_service', 'A', 'select 1 from public.audit_log')),
  ('dashboard_service no ve filas audit_log de platform_admin (impersonación, §2.11.4)', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.audit_log where actor_type = 'platform_admin'$q$)),
  ('dashboard_service no ve audit_log de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.audit_log where tenant_id = '{B}'$q$)),
  ('dashboard_service no ve audit_log sin tenant', '0',
    pg_temp.contar('dashboard_service', 'A', 'select 1 from public.audit_log where tenant_id is null')),
  ('dashboard_service no inserta audit_log directamente', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$insert into public.audit_log (actor_type, action) values ('staff', 'x')$q$)),
  ('dashboard_service no actualiza audit_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.audit_log set action = 'x'$q$)),
  ('dashboard_service no borra audit_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.audit_log'));

insert into casos (descr, esperado, real) values
  ('dashboard_service ve notifications_log propio', '1',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.notifications_log where tenant_id = '{A}'$q$)),
  ('dashboard_service no ve notifications_log de B', '0',
    pg_temp.contar('dashboard_service', 'A', $q$select 1 from public.notifications_log where tenant_id = '{B}'$q$)),
  ('dashboard_service no inserta notifications_log para un end_customer de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.notifications_log (tenant_id, end_customer_id, channel)
         values ('{B}', '00000000-0000-0000-0000-0000000000e2', 'telegram')$q$)),
  ('dashboard_service no actualiza notifications_log de B', 'ok 0',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.notifications_log set retry_count = 9 where tenant_id = '{B}'$q$)),
  ('dashboard_service no borra notifications_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('dashboard_service', 'A', 'delete from public.notifications_log'));

insert into casos (descr, esperado, real)
select 'dashboard_service sin SELECT en ' || t, 'err 42501 permission denied%',
       pg_temp.contar('dashboard_service', 'A', format('select * from public.%I', t))
  from unnest(array['payments', 'inbound_messages', 'bot_sessions', 'platform_settings', 'platform_admins']) t;

-- ---------------------------------------------------------------------------
-- platform_admin_readonly: sujeto al mismo aislamiento por tenant que dashboard_service (sin bypass por
-- current_user): sin contexto no ve nada; con contexto A ve solo A y nada de B. No escribe nada.
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real) values
  ('platform_admin_readonly sin contexto no ve tenants', '0',
    pg_temp.contar('platform_admin_readonly', null,
      $q$select 1 from public.tenants where id in ('{A}', '{B}')$q$)),
  ('platform_admin_readonly sin contexto no ve tenant_users', '0',
    pg_temp.contar('platform_admin_readonly', null, 'select 1 from public.tenant_users')),
  ('platform_admin_readonly sin contexto no ve end_customers', '0',
    pg_temp.contar('platform_admin_readonly', null, 'select 1 from public.end_customers')),
  ('platform_admin_readonly sin contexto no ve notifications_log', '0',
    pg_temp.contar('platform_admin_readonly', null, 'select 1 from public.notifications_log')),
  ('platform_admin_readonly sin contexto no ve tenant_user_permissions', '0',
    pg_temp.contar('platform_admin_readonly', null, 'select 1 from public.tenant_user_permissions')),
  ('platform_admin_readonly sin contexto no ve tenant_integrations', '0',
    pg_temp.contar('platform_admin_readonly', null, 'select id, tenant_id from public.tenant_integrations')),
  ('platform_admin_readonly con contexto A ve solo la fila de A en tenants', '1',
    pg_temp.contar('platform_admin_readonly', 'A', 'select 1 from public.tenants')),
  ('platform_admin_readonly con contexto A no ve el tenant B', '0',
    pg_temp.contar('platform_admin_readonly', 'A', $q$select 1 from public.tenants where id = '{B}'$q$)),
  ('platform_admin_readonly con contexto A ve tenant_users de A', '2',
    pg_temp.contar('platform_admin_readonly', 'A', $q$select 1 from public.tenant_users where tenant_id = '{A}'$q$)),
  ('platform_admin_readonly con contexto A no ve tenant_users de B', '0',
    pg_temp.contar('platform_admin_readonly', 'A', $q$select 1 from public.tenant_users where tenant_id = '{B}'$q$)),
  ('platform_admin_readonly con contexto A ve solo los end_customers de A', '1',
    pg_temp.contar('platform_admin_readonly', 'A', 'select 1 from public.end_customers')),
  ('platform_admin_readonly con contexto A no ve end_customers de B', '0',
    pg_temp.contar('platform_admin_readonly', 'A', $q$select 1 from public.end_customers where tenant_id = '{B}'$q$)),
  ('platform_admin_readonly con contexto A ve solo el notifications_log de A', '1',
    pg_temp.contar('platform_admin_readonly', 'A', 'select 1 from public.notifications_log')),
  ('platform_admin_readonly con contexto A ve solo tenant_user_permissions de A', '1',
    pg_temp.contar('platform_admin_readonly', 'A', 'select 1 from public.tenant_user_permissions')),
  ('platform_admin_readonly con contexto B ve solo tenant_user_permissions de B', '1',
    pg_temp.contar('platform_admin_readonly', 'B', $q$select 1 from public.tenant_user_permissions where tenant_id = '{B}'$q$)),
  ('platform_admin_readonly con contexto A ve solo tenant_integrations de A (sin secret_id/config)', '1',
    pg_temp.contar('platform_admin_readonly', 'A', 'select id, tenant_id from public.tenant_integrations')),
  ('platform_admin_readonly no lee tenant_integrations.secret_id', 'err 42501 permission denied%',
    pg_temp.contar('platform_admin_readonly', 'A', 'select secret_id from public.tenant_integrations')),
  ('platform_admin_readonly no lee tenant_integrations.config', 'err 42501 permission denied%',
    pg_temp.contar('platform_admin_readonly', 'A', 'select config from public.tenant_integrations'));

insert into casos (descr, esperado, real)
select 'platform_admin_readonly sin SELECT en ' || t, 'err 42501 permission denied%',
       pg_temp.contar('platform_admin_readonly', null, format('select * from public.%I', t))
  from unnest(array['platform_settings', 'platform_admins', 'audit_log', 'inbound_messages',
                    'bot_sessions', 'payments']) t;

-- INSERT/UPDATE/DELETE: permission denied en las 13 tablas de plataforma
create temp table tablas_plataforma (t text, col text);
insert into tablas_plataforma
select t, (select c.column_name::text from information_schema.columns c
            where c.table_schema = 'public' and c.table_name = t order by c.ordinal_position limit 1)
  from unnest(array[
    'tenants', 'tenant_users', 'permissions', 'tenant_user_permissions', 'platform_settings',
    'platform_admins', 'tenant_integrations', 'end_customers', 'audit_log', 'inbound_messages',
    'bot_sessions', 'notifications_log', 'payments']) t;

insert into casos (descr, esperado, real)
select 'platform_admin_readonly: ' || o.op || ' en ' || p.t || ' falla con permission denied',
       'err 42501 permission denied%',
       pg_temp.ejecutar('platform_admin_readonly', null,
         case o.op
           when 'INSERT' then format('insert into public.%I default values', p.t)
           when 'UPDATE' then format('update public.%I set %I = %I', p.t, p.col, p.col)
           else format('delete from public.%I', p.t)
         end)
  from tablas_plataforma p
  cross join (values ('INSERT'), ('UPDATE'), ('DELETE')) o(op);

-- ---------------------------------------------------------------------------
-- purge_job y scheduler_job: sin grants sobre tablas (solo EXECUTE de funciones definer)
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select r.rol || ': ' || o.op || ' en ' || p.t || ' falla con permission denied',
       'err 42501 permission denied%',
       pg_temp.ejecutar(r.rol, 'A',
         case o.op
           when 'SELECT' then format('select * from public.%I', p.t)
           when 'INSERT' then format('insert into public.%I default values', p.t)
           when 'UPDATE' then format('update public.%I set %I = %I', p.t, p.col, p.col)
           else format('delete from public.%I', p.t)
         end)
  from tablas_plataforma p
  cross join (values ('purge_job'), ('scheduler_job')) r(rol)
  cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) o(op);

select plan((select count(*)::int from casos));

select alike(real, esperado, descr) from casos order by n;

select * from finish();
rollback;
