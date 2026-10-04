-- tenant_integrations: unicidades globales y por tenant (§2.6, §2.10).
begin;
select plan(9);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'int-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'int-b');

insert into public.tenant_integrations (id, tenant_id, integration_type, secret_id, config) values
  ('00000000-0000-0000-0000-000000000011', '00000000-0000-0000-0000-0000000000a1', 'telegram_bot',
   '00000000-0000-0000-0000-0000000000f1', '{"webhook_key": "wk-1"}'),
  ('00000000-0000-0000-0000-000000000012', '00000000-0000-0000-0000-0000000000a1', 'whatsapp_business',
   '00000000-0000-0000-0000-0000000000f2', '{"phone_number_id": "pn-1"}');

select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000a2', 'telegram_bot', '{"webhook_key": "wk-1"}')$$,
  '23505', null, 'dos tenants no pueden compartir webhook_key');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000a2', 'whatsapp_business', '{"phone_number_id": "pn-1"}')$$,
  '23505', null, 'dos tenants no pueden compartir phone_number_id');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, secret_id)
    values ('00000000-0000-0000-0000-0000000000a2', 'mercadopago', '00000000-0000-0000-0000-0000000000f1')$$,
  '23505', null, 'dos tenants no pueden compartir secret_id');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000a2', 'telegram_bot', '{"webhook_key": "wk-2"}')$$,
  'otro tenant con identificadores distintos sí puede');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, secret_id)
    values ('00000000-0000-0000-0000-0000000000a2', 'google_calendar', null),
           ('00000000-0000-0000-0000-0000000000a2', 'mercadopago', null)$$,
  'varias filas con secret_id NULL no chocan');

-- índice parcial: integraciones de tenant (professional_id null)
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000a1', 'telegram_bot')$$,
  '23505', null, 'un tenant no puede tener dos integraciones del mismo tipo sin profesional');
-- la unicidad por profesional (y su FK compuesta hacia professionals) la cubre el test del dominio

select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000a2', 'mercadopago', '[]')$$,
  '23514', null, 'config debe ser un objeto JSON');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000f9', 'mercadopago')$$,
  '23503', null, 'tenant_id debe existir');
select has_index('public', 'tenant_integrations', 'tenant_integrations_secret_id_key',
  'unique(secret_id) existe');

select * from finish();
rollback;
