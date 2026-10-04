-- end_customers: check canal/identificador, unicidad e índices (§2.3, §2.10).
begin;
select plan(9);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'ec-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'ec-b');

select throws_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'telegram', null)$$,
  '23514', null, 'el check rechaza un canal sin identificador');
select throws_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', null, 'u1')$$,
  '23514', null, 'el check rechaza un identificador sin canal');
select throws_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_hash)
    values ('00000000-0000-0000-0000-0000000000a1', null, 'hash')$$,
  '23514', null, 'el check rechaza un hash sin canal');
select lives_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_hash)
    values ('00000000-0000-0000-0000-0000000000a1', 'whatsapp', 'hash')$$,
  'canal con hash (fila anonimizada) es válido');
select lives_ok(
  $$insert into public.end_customers (tenant_id, phone, name)
    values ('00000000-0000-0000-0000-0000000000a1', '+5491100000001', 'Manual 1'),
           ('00000000-0000-0000-0000-0000000000a1', '+5491100000002', 'Manual 2')$$,
  'dos altas manuales (channel y channel_user_id null) no chocan');

insert into public.end_customers (tenant_id, channel, channel_user_id)
values ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');
select throws_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1')$$,
  '23505', null, 'no se repite (tenant, canal, channel_user_id)');
select lives_ok(
  $$insert into public.end_customers (tenant_id, channel, channel_user_id)
    values ('00000000-0000-0000-0000-0000000000a2', 'telegram', 'u1')$$,
  'la misma persona puede ser cliente de otro tenant (fila independiente)');
select lives_ok(
  $$insert into public.end_customers (tenant_id, phone, name, extra_data, deleted_at)
    values ('00000000-0000-0000-0000-0000000000a1', null, null, '{}', now())$$,
  'phone y name son nullable (anonymize_end_customer los pone en null)');
select throws_ok(
  $$insert into public.end_customers (tenant_id, extra_data)
    values ('00000000-0000-0000-0000-0000000000a1', '[]')$$,
  '23514', null, 'extra_data debe ser un objeto JSON');

select * from finish();
rollback;
