-- Migración de plataforma 2: estructura y enums-como-texto (§1.7.7, §2.10).
begin;
select plan(35);

select has_table('public', t, 'existe la tabla ' || t)
from unnest(array['tenant_integrations', 'end_customers', 'audit_log', 'inbound_messages',
                  'bot_sessions', 'notifications_log', 'payments']) as t;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'ops-a');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');

-- tenant_integrations.integration_type
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000a1', 'sms')$$,
  '23514', null, 'tenant_integrations.integration_type rechaza valores fuera del check');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000a1', null)$$,
  '23502', null, 'tenant_integrations.integration_type rechaza NULL');

-- end_customers.channel
select throws_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'sms', 'x')$$,
  '23514', null, 'end_customers.channel rechaza valores fuera del check');

-- audit_log.actor_type
select throws_ok(
  $$insert into public.audit_log (actor_type, action) values ('robot', 'x')$$,
  '23514', null, 'audit_log.actor_type rechaza valores fuera del check');
select throws_ok(
  $$insert into public.audit_log (actor_type, action) values (null, 'x')$$,
  '23502', null, 'audit_log.actor_type rechaza NULL');
select lives_ok(
  $$insert into public.audit_log (tenant_id, actor_type, action)
    values (null, 'platform_admin', 'editar_platform_settings')$$,
  'audit_log.tenant_id es nullable (acciones sin tenant único)');

-- inbound_messages.channel
select throws_ok(
  $$insert into public.inbound_messages (tenant_id, channel, external_message_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'sms', 'm1')$$,
  '23514', null, 'inbound_messages.channel rechaza valores fuera del check');
select throws_ok(
  $$insert into public.inbound_messages (tenant_id, channel, external_message_id)
    values ('00000000-0000-0000-0000-0000000000a1', null, 'm1')$$,
  '23502', null, 'inbound_messages.channel rechaza NULL');
select lives_ok(
  $$insert into public.inbound_messages (tenant_id, channel, external_message_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'm1')$$,
  'inbound_messages acepta un mensaje válido');
select throws_ok(
  $$insert into public.inbound_messages (tenant_id, channel, external_message_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'm1')$$,
  '23505', null, 'inbound_messages deduplica la reentrega (misma clave primaria)');

-- bot_sessions.channel
select throws_ok(
  $$insert into public.bot_sessions (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'sms', 'u1')$$,
  '23514', null, 'bot_sessions.channel rechaza valores fuera del check');
select throws_ok(
  $$insert into public.bot_sessions (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', null, 'u1')$$,
  '23502', null, 'bot_sessions.channel rechaza NULL');

-- notifications_log.channel / status
select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'sms')$$,
  '23514', null, 'notifications_log.channel rechaza valores fuera del check');
select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', null)$$,
  '23502', null, 'notifications_log.channel rechaza NULL');
select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'telegram', 'perdido')$$,
  '23514', null, 'notifications_log.status rechaza valores fuera del check');
select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'telegram', null)$$,
  '23502', null, 'notifications_log.status rechaza NULL');
select lives_ok(
  $$insert into public.notifications_log (id, tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1',
            '00000000-0000-0000-0000-0000000000c1', 'telegram')$$,
  'notifications_log acepta una notificación válida');
select is(
  (select status from public.notifications_log where id = '00000000-0000-0000-0000-0000000000d1'),
  'pending', 'el default de notifications_log.status es pending');

-- payments
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'cosa', 10, 'pending')$$,
  '23514', null, 'payments.payable_type rechaza valores fuera del check');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', null, 10, 'pending')$$,
  '23502', null, 'payments.payable_type rechaza NULL');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 10, 'raro')$$,
  '23514', null, 'payments.status rechaza valores fuera del check');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 10, null)$$,
  '23502', null, 'payments.status rechaza NULL');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status, provider)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 10, 'pending', 'paypal')$$,
  '23514', null, 'payments.provider rechaza valores fuera del check');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status, provider)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 10, 'pending', null)$$,
  '23502', null, 'payments.provider rechaza NULL');
select lives_ok(
  $$insert into public.payments (id, tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1',
            'booking_pack', 0, 'pending')$$,
  'payments acepta amount = 0');
select is(
  (select provider from public.payments where id = '00000000-0000-0000-0000-0000000000e1'),
  'mercadopago', 'el default de payments.provider es mercadopago');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', -1, 'pending')$$,
  '23514', null, 'payments rechaza amount negativo');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', null, 'pending')$$,
  '23502', null, 'payments.amount rechaza NULL');

select * from finish();
rollback;
