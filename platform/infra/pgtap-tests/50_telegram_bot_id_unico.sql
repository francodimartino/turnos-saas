-- tenant_integrations: el bot_id de Telegram es único entre tenants.
begin;
select plan(6);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000b1', 'A', 'bot-a'),
  ('00000000-0000-0000-0000-0000000000b2', 'B', 'bot-b'),
  ('00000000-0000-0000-0000-0000000000b3', 'C', 'bot-c');

insert into public.tenant_integrations (tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-0000000000b1', 'telegram_bot', '{"webhook_key": "bk-1", "bot_id": "111"}'),
  ('00000000-0000-0000-0000-0000000000b3', 'telegram_bot', '{"webhook_key": "bk-3"}');

select has_index('public', 'tenant_integrations', 'tenant_integrations_telegram_bot_id_uidx',
  'índice único de bot_id existe');

select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000b2', 'telegram_bot', '{"webhook_key": "bk-2", "bot_id": "111"}')$$,
  '23505', null, 'dos tenants no pueden compartir bot_id');

select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, config)
    values ('00000000-0000-0000-0000-0000000000b2', 'telegram_bot', '{"webhook_key": "bk-2", "bot_id": "222"}')$$,
  'bot_id distintos en tenants distintos conviven');

select lives_ok(
  $$update public.tenant_integrations set config = '{"webhook_key": "bk-1", "bot_id": "111", "x": 1}'
    where tenant_id = '00000000-0000-0000-0000-0000000000b1' and integration_type = 'telegram_bot'$$,
  'el mismo tenant puede volver a guardar el mismo bot_id');

select lives_ok(
  $$update public.tenant_integrations set config = '{"webhook_key": "bk-3"}'
    where tenant_id = '00000000-0000-0000-0000-0000000000b3' and integration_type = 'telegram_bot'$$,
  'filas sin bot_id conviven');

select is(
  (select count(*)::int from public.tenant_integrations where integration_type = 'telegram_bot' and not (config ? 'bot_id')),
  1, 'la fila sin bot_id sigue ahí');

select * from finish();
rollback;
