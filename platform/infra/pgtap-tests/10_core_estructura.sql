-- Migración de plataforma 1: estructura, roles de login, checks y datos semilla.
begin;
select plan(32);

select has_table('public', t, 'existe la tabla ' || t)
from unnest(array['tenants', 'tenant_users', 'permissions', 'tenant_user_permissions',
                  'platform_settings', 'platform_admins']) as t;

select is(
  (select count(*) from pg_roles
    where rolname in ('bot_service', 'dashboard_service', 'platform_admin',
                      'platform_admin_readonly', 'purge_job', 'scheduler_job')
      and rolcanlogin and not rolbypassrls and not rolsuper),
  6::bigint,
  'los 6 roles de login existen, con LOGIN, sin BYPASSRLS y sin superuser');

select is(
  (select value from public.platform_settings where key = 'tenant_deletion_grace_period_days'),
  '90'::jsonb,
  'platform_settings siembra tenant_deletion_grace_period_days = 90');

select is(
  (select array_agg(key order by key) from public.permissions),
  array['approve_bookings', 'delete_customers', 'edit_prices', 'manage_customers', 'manage_schedule',
        'manage_staff', 'view_bookings'],
  'el catálogo permissions tiene las 7 claves (view_reports fue retirado; incluye manage_customers, delete_customers, manage_schedule)');

select col_not_null('public', 'tenants', 'plan', 'tenants.plan es not null');
select col_not_null('public', 'tenants', 'status', 'tenants.status es not null');
select col_not_null('public', 'tenant_users', 'is_owner', 'tenant_users.is_owner es not null');
select col_not_null('public', 'tenant_users', 'is_active', 'tenant_users.is_active es not null');
select col_is_null('public', 'tenant_users', 'auth_user_id', 'tenant_users.auth_user_id es nullable');

select throws_ok(
  $$insert into public.tenants (name, slug, plan) values ('X', 'x-plan', 'enterprise')$$,
  '23514', null, 'tenants.plan rechaza valores fuera del check');
select throws_ok(
  $$insert into public.tenants (name, slug, status) values ('X', 'x-status', 'borrado')$$,
  '23514', null, 'tenants.status rechaza valores fuera del check');
select throws_ok(
  $$insert into public.tenants (name, slug, status) values ('X', 'x-null', null)$$,
  '23502', null, 'tenants.status rechaza NULL');
select throws_ok(
  $$insert into public.tenants (name, slug, plan) values ('X', 'x-plan-null', null)$$,
  '23502', null, 'tenants.plan rechaza NULL');
select throws_ok(
  $$insert into public.tenants (name, slug, status) values ('X', 'x-inactive', 'inactive')$$,
  '23514', null, 'un tenant inactive sin deactivated_at es rechazado');
select lives_ok(
  $$insert into public.tenants (name, slug, status, deactivated_at)
    values ('X', 'x-inactive-ok', 'inactive', now())$$,
  'un tenant inactive con deactivated_at es aceptado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('X', 'x-settings-array', '[]')$$,
  '23514', null, 'tenants.settings debe ser un objeto JSON');

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'estructura-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'estructura-b');
select throws_ok(
  $$insert into public.tenants (name, slug) values ('Otro', 'estructura-a')$$,
  '23505', null, 'tenants.slug es único');

insert into public.tenant_users (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1');
select throws_ok(
  $$insert into public.tenant_users (tenant_id, is_owner) values (null, true)$$,
  '23502', null, 'tenant_users.tenant_id es obligatorio');
select throws_ok(
  $$insert into public.tenant_users (tenant_id, auth_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000dead')$$,
  '23503', null, 'tenant_users.auth_user_id referencia auth.users');

select lives_ok(
  $$insert into auth.users (id) values ('00000000-0000-0000-0000-0000000000c1')$$,
  'se puede crear una cuenta de prueba en auth.users');
insert into public.tenant_users (id, tenant_id, auth_user_id) values
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1',
   '00000000-0000-0000-0000-0000000000c1');
select throws_ok(
  $$insert into public.tenant_users (tenant_id, auth_user_id)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c1')$$,
  '23505', null, 'auth_user_id es único: una persona, un solo tenant (§2.7)');

select ok(
  exists (
    select 1 from pg_constraint c
    where c.conrelid = 'public.tenant_users'::regclass and c.contype = 'u'
      and (select array_agg(a.attname::text order by a.attname) from pg_attribute a
            where a.attrelid = c.conrelid and a.attnum = any (c.conkey)) = array['id', 'tenant_id']),
  'tenant_users tiene unique (tenant_id, id) para las FKs compuestas');
select fk_ok('public', 'tenant_user_permissions', array['tenant_id', 'tenant_user_id'],
  'public', 'tenant_users', array['tenant_id', 'id'],
  'tenant_user_permissions referencia (tenant_id, id) de tenant_users');

-- purging (§2.2) y gracia mínima de 30 días forzada en la base
select lives_ok(
  $$insert into public.tenants (name, slug, status, deactivated_at)
    values ('X', 'x-purging-ok', 'purging', now() - interval '100 days')$$,
  'un tenant purging con deactivated_at es aceptado (el check de status admite purging)');
select throws_ok(
  $$insert into public.tenants (name, slug, status) values ('X', 'x-purging', 'purging')$$,
  '23514', null, 'un tenant purging sin deactivated_at es rechazado');
select throws_ok(
  $$update public.platform_settings set value = '29'::jsonb where key = 'tenant_deletion_grace_period_days'$$,
  '23514', null, 'la base rechaza una gracia menor a 30 días');
select lives_ok(
  $$update public.platform_settings set value = '30'::jsonb where key = 'tenant_deletion_grace_period_days'$$,
  'la base acepta una gracia de exactamente 30 días');

select * from finish();
rollback;
