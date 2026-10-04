-- Policies explícitas de las tablas sin tenant_id directo y acceso de platform_admin (§3.1.6, §3.2).
-- Mismo mecanismo que 32_aislamiento_tenant.sql: caller real con `set session authorization`,
-- helpers temporales que revierten los efectos y aserciones de pgTAP al final.
--
--   tenants de prueba: A = ...a1, B = ...a2. En los SQL de los casos, {A} y {B} son esos ids.
begin;

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
  execute format('set session authorization %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute 'select count(*) from (' || v_sql || ') q' into v_n;
    reset session authorization;
    return v_n::text;
  exception when others then
    reset session authorization;
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
  execute format('set session authorization %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute v_sql;
    get diagnostics v_n = row_count;
    raise exception 'revertir' using errcode = 'ZZ001';
  exception when others then
    reset session authorization;
    if sqlstate = 'ZZ001' then
      return 'ok ' || v_n;
    end if;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- ---------------------------------------------------------------------------
-- Datos de prueba (como superusuario)
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'pol-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'pol-b');
insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000c1'),
  ('00000000-0000-0000-0000-0000000000c2'),
  ('00000000-0000-0000-0000-0000000000c3');
insert into public.platform_admins (auth_user_id) values
  ('00000000-0000-0000-0000-0000000000c1'),
  ('00000000-0000-0000-0000-0000000000c2');
insert into public.tenant_users (id, tenant_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', true),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', true),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a1', false);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');
insert into public.tenant_integrations (tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-0000000000a1', 'telegram_bot', '{"webhook_key": "pol-k-a"}'),
  ('00000000-0000-0000-0000-0000000000a2', 'telegram_bot', '{"webhook_key": "pol-k-b"}');
insert into public.audit_log (tenant_id, actor_type, action) values
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'pol_accion_a'),
  ('00000000-0000-0000-0000-0000000000a1', 'platform_admin', 'pol_impersonate_view'),
  ('00000000-0000-0000-0000-0000000000a2', 'staff', 'pol_accion_b'),
  (null, 'platform_admin', 'pol_editar_platform_settings');
insert into public.notifications_log (tenant_id, end_customer_id, channel) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000e1', 'telegram'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000e2', 'telegram');

create temp table casos (n serial, descr text, esperado text, real text);

-- ---------------------------------------------------------------------------
-- platform_admin (sin contexto de tenant; una variante con contexto A)
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real) values
  ('platform_admin ve todos los tenants', '2',
    pg_temp.contar('platform_admin', null, $q$select 1 from public.tenants where id in ('{A}', '{B}')$q$)),
  ('platform_admin ve todos los tenants aunque haya contexto de otro tenant', '2',
    pg_temp.contar('platform_admin', 'A', $q$select 1 from public.tenants where id in ('{A}', '{B}')$q$)),
  ('platform_admin da de alta un tenant', 'ok 1',
    pg_temp.ejecutar('platform_admin', null, $q$insert into public.tenants (name, slug) values ('n', 'pol-nuevo')$q$)),
  ('platform_admin suspende un tenant (transición válida)', 'ok 1',
    pg_temp.ejecutar('platform_admin', null, $q$update public.tenants set status = 'suspended' where id = '{A}'$q$)),
  ('platform_admin: el trigger rechaza una transición inválida (active -> purged)', 'err 23514%',
    pg_temp.ejecutar('platform_admin', null, $q$update public.tenants set status = 'purged' where id = '{A}'$q$)),
  ('platform_admin edita tenants.billing_notes', 'ok 1',
    pg_temp.ejecutar('platform_admin', null, $q$update public.tenants set billing_notes = 'pagó' where id = '{B}'$q$)),
  ('platform_admin no borra tenants', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, 'delete from public.tenants'));

insert into casos (descr, esperado, real) values
  ('platform_admin lee platform_settings', '1',
    pg_temp.contar('platform_admin', null,
      $q$select 1 from public.platform_settings where key = 'tenant_deletion_grace_period_days'$q$)),
  ('platform_admin edita platform_settings', 'ok 1',
    pg_temp.ejecutar('platform_admin', null,
      $q$update public.platform_settings set value = '120' where key = 'tenant_deletion_grace_period_days'$q$)),
  ('platform_admin agrega una clave a platform_settings', 'ok 1',
    pg_temp.ejecutar('platform_admin', null, $q$insert into public.platform_settings (key, value) values ('otra', '1')$q$)),
  ('platform_admin no borra platform_settings', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, 'delete from public.platform_settings')),
  ('platform_admin lee platform_admins', '2',
    pg_temp.contar('platform_admin', null, 'select 1 from public.platform_admins')),
  ('platform_admin agrega un segundo platform_admin', 'ok 1',
    pg_temp.ejecutar('platform_admin', null,
      $q$insert into public.platform_admins (auth_user_id) values ('00000000-0000-0000-0000-0000000000c3')$q$)),
  ('platform_admin quita un platform_admin si queda otro', 'ok 1',
    pg_temp.ejecutar('platform_admin', null,
      $q$delete from public.platform_admins where auth_user_id = '00000000-0000-0000-0000-0000000000c1'$q$));

insert into casos (descr, esperado, real) values
  ('platform_admin ve tenant_users de todos los tenants', '3',
    pg_temp.contar('platform_admin', null, $q$select 1 from public.tenant_users where tenant_id in ('{A}', '{B}')$q$)),
  ('platform_admin ve tenant_users de B aunque el contexto sea A', '1',
    pg_temp.contar('platform_admin', 'A', $q$select 1 from public.tenant_users where tenant_id = '{B}'$q$)),
  ('platform_admin crea el primer owner de un tenant (create_tenant)', 'ok 1',
    pg_temp.ejecutar('platform_admin', null, $q$insert into public.tenant_users (tenant_id, is_owner) values ('{B}', true)$q$)),
  ('platform_admin actualiza tenant_users', 'ok 1',
    pg_temp.ejecutar('platform_admin', null,
      $q$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-0000000000b3'$q$)),
  ('platform_admin no borra tenant_users', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, 'delete from public.tenant_users'));

insert into casos (descr, esperado, real) values
  ('platform_admin ve tenant_integrations de todos los tenants', '2',
    pg_temp.contar('platform_admin', null, $q$select 1 from public.tenant_integrations where tenant_id in ('{A}', '{B}')$q$)),
  ('platform_admin no escribe tenant_integrations directo (usa store_tenant_integration)', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null,
      $q$insert into public.tenant_integrations (tenant_id, integration_type) values ('{A}', 'mercadopago')$q$)),
  ('platform_admin lee audit_log de todos los tenants y de sistema (incluye sus propias acciones)', '4',
    pg_temp.contar('platform_admin', null, $q$select 1 from public.audit_log where action like 'pol\_%'$q$)),
  ('platform_admin no escribe audit_log directo', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, $q$insert into public.audit_log (actor_type, action) values ('platform_admin', 'x')$q$)),
  ('platform_admin no actualiza audit_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, $q$update public.audit_log set action = 'x'$q$)),
  ('platform_admin lee notifications_log de todos los tenants', '2',
    pg_temp.contar('platform_admin', null, $q$select 1 from public.notifications_log where tenant_id in ('{A}', '{B}')$q$)),
  ('platform_admin no escribe notifications_log', 'err 42501 permission denied%',
    pg_temp.ejecutar('platform_admin', null, $q$update public.notifications_log set status = 'sent'$q$));

-- Sin acceso directo a datos operativos de un tenant (para eso existe platform_admin_readonly)
insert into casos (descr, esperado, real)
select 'platform_admin sin SELECT en ' || t, 'err 42501 permission denied%',
       pg_temp.contar('platform_admin', null, format('select * from public.%I', t))
  from unnest(array['end_customers', 'payments', 'bot_sessions', 'inbound_messages',
                    'tenant_user_permissions', 'permissions']) t;

-- ---------------------------------------------------------------------------
-- Tablas solo de platform_admin: el resto de los roles no tiene grant (denegado)
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select r.rol || ' no lee ' || t, 'err 42501 permission denied%',
       pg_temp.contar(r.rol, 'A', format('select * from public.%I', t))
  from unnest(array['platform_settings', 'platform_admins']) t
 cross join unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly',
                         'purge_job', 'scheduler_job']) r(rol);

-- Defensa en profundidad: aunque un grant se otorgara por error, la ausencia de policy
-- seguiría denegando (filas invisibles). El grant es transitorio y se revierte con el rollback.
grant select on public.platform_settings, public.platform_admins to dashboard_service;
insert into casos (descr, esperado, real)
select 'sin policy para dashboard_service, ' || t || ' queda vacía aunque haya grant', '0',
       pg_temp.contar('dashboard_service', 'A', format('select * from public.%I', t))
  from unnest(array['platform_settings', 'platform_admins']) t;
revoke select on public.platform_settings, public.platform_admins from dashboard_service;

-- permissions: catálogo legible, sin datos de tenant
insert into casos (descr, esperado, real) values
  ('bot_service sin grant no lee permissions (la policy sola no alcanza)', 'err 42501 permission denied%',
    pg_temp.contar('bot_service', 'A', 'select * from public.permissions')),
  ('platform_admin_readonly lee permissions', '1',
    pg_temp.contar('platform_admin_readonly', null, $q$select 1 from public.permissions where key = 'view_bookings'$q$)),
  ('dashboard_service lee permissions sin necesitar contexto de tenant (catálogo)', '1',
    pg_temp.contar('dashboard_service', null, $q$select 1 from public.permissions where key = 'view_bookings'$q$));

select plan((select count(*)::int from casos) + 9);

select like(real, esperado, descr) from casos order by n;

-- ---------------------------------------------------------------------------
-- Estructura de las policies (§3.1.3, §3.1.6)
-- ---------------------------------------------------------------------------
select is_empty(
  $$select t
      from unnest(array['tenants', 'tenant_users', 'tenant_user_permissions', 'tenant_integrations',
                        'end_customers', 'audit_log', 'inbound_messages', 'bot_sessions',
                        'notifications_log', 'payments']) t
     where not exists (
       select 1 from pg_policies p
        where p.schemaname = 'public' and p.tablename = t and p.policyname = 'tenant_isolation')$$,
  'las tablas con tenant (y tenants) tienen la policy tenant_isolation');

select like(
  (select qual from pg_policies
    where schemaname = 'public' and tablename = 'end_customers' and policyname = 'tenant_isolation'),
  '%nullif(current_setting(''app.current_tenant_id''%true)%',
  'tenant_isolation usa nullif(current_setting(app.current_tenant_id, true), ...)');

select like(
  (select qual from pg_policies
    where schemaname = 'public' and tablename = 'end_customers' and policyname = 'tenant_isolation'),
  '%platform_admin_readonly%',
  'tenant_isolation reconoce el bypass de platform_admin_readonly por current_user');

select is(
  (select count(*) from pg_policies
    where schemaname = 'public' and tablename in ('platform_settings', 'platform_admins')
      and roles::text = '{platform_admin}' and cmd = 'ALL'),
  2::bigint, 'platform_settings y platform_admins: policy explícita solo para platform_admin');

select is(
  (select count(*) from pg_policies
    where schemaname = 'public' and tablename in ('platform_settings', 'platform_admins')),
  2::bigint, 'platform_settings y platform_admins no tienen ninguna otra policy');

select is(
  (select count(*) from pg_policies
    where schemaname = 'public' and tablename in ('tenants', 'tenant_users', 'tenant_integrations')
      and roles::text = '{platform_admin}' and cmd = 'ALL'),
  3::bigint, 'tenants, tenant_users y tenant_integrations tienen su policy explícita to platform_admin');

select is(
  (select cmd from pg_policies
    where schemaname = 'public' and tablename = 'notifications_log' and roles::text = '{platform_admin}'),
  'SELECT', 'notifications_log: platform_admin solo con SELECT');

select is(
  (select permissive from pg_policies
    where schemaname = 'public' and tablename = 'audit_log'
      and policyname = 'sin_acciones_de_platform_admin' and roles::text = '{dashboard_service}'),
  'RESTRICTIVE', 'audit_log: dashboard_service queda restringido a actor_type <> platform_admin');

select is_empty(
  $$select rolname from pg_roles
     where rolname in ('bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                       'purge_job', 'scheduler_job')
       and (rolbypassrls or rolsuper)$$,
  'ningún rol de login tiene BYPASSRLS ni es superusuario (cada acceso de plataforma es una policy)');

select * from finish();
rollback;
