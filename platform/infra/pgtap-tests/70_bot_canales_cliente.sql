-- El bot identifica por canales_cliente (§2.12): la anonimización saca el canal de chat de ahí y deja el pseudónimo
-- para el recontacto (§2.3), y reactivate_end_customer solo reactiva con el identificador que corresponde.
-- La reactivación completa (con el rol bot_service real y su audit_log) está en
-- apps/bot/turnos_bot/tests/test_identidad_integracion.py: acá `set role` no cambia session_user.
begin;
select plan(14);

do $$
begin
  execute 'grant bot_service to current_user with inherit false, set true';
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

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
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000f1', 'A', 'bcc-a'),
  ('00000000-0000-0000-0000-0000000000f2', 'B', 'bcc-b');

-- fichas sin columnas viejas: el canal de Telegram vive solo en canales_cliente
insert into public.end_customers (id, tenant_id, phone, name) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000f1', '+5491111111111', 'Ana'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000f1', '+5491122222222', 'Solo web'),
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000f2', '+5491133333333', 'Otra');
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'tg-a1', now()),
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'email', 'ana@ejemplo.com', now()),
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a2', 'email', 'web@ejemplo.com', now()),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'tg-a1', now());
insert into public.bot_sessions (tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000f1', 'telegram', 'tg-a1'),
  ('00000000-0000-0000-0000-0000000000f2', 'telegram', 'tg-a1');

select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000000f1', true);
select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000a1', 'bot',
  '00000000-0000-0000-0000-0000000000a1');
select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000a2', 'bot', null);

select is((select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-0000000000a1'),  -- 1
  public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000f1', 'telegram', 'tg-a1'),
  'el pseudónimo sale del canal de Telegram de canales_cliente');
select is((select channel from public.end_customers where id = '00000000-0000-0000-0000-0000000000a1'),  -- 2
  'telegram', 'la ficha queda con su canal para el recontacto');
select is((select count(*) from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-0000000000a1'),  -- 3
  0::bigint, 'los canales de la ficha se borran (Telegram y email)');
select is((select count(*) from public.bot_sessions where tenant_id = '00000000-0000-0000-0000-0000000000f1'),  -- 4
  0::bigint, 'la sesión del usuario de Telegram se borra');
select is((select count(*) from public.bot_sessions where tenant_id = '00000000-0000-0000-0000-0000000000f2'),  -- 5
  1::bigint, 'la sesión del mismo usuario en otro tenant no se toca');
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000000f2'),  -- 6
  1::bigint, 'los canales del otro tenant no se tocan');
select is((select channel from public.end_customers where id = '00000000-0000-0000-0000-0000000000a2'),  -- 7
  null, 'una ficha solo con email no tiene canal de chat ni pseudónimo');
select is((select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-0000000000a2'),  -- 8
  null, 'sin canal de chat no se calcula pseudónimo');

select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000f1',  -- 9
  $$select id::text from public.find_dormant_end_customer('telegram', 'tg-a1')$$),
  '00000000-0000-0000-0000-0000000000a1', 'el recontacto encuentra la ficha por el usuario de Telegram');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000f2',  -- 10
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'tg-a1')$$),
  '0', 'otro tenant no la ve');

-- reactivate_end_customer: los caminos que no llegan al audit_log
select ok(pg_temp.como('bot_service', null,  -- 11
  $$select count(*)::text from public.reactivate_end_customer('00000000-0000-0000-0000-0000000000a1', 'telegram', 'tg-a1')$$)
  like 'err 42501%contexto de tenant%', 'sin contexto de tenant: rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000f1',  -- 12
  $$select count(*)::text from public.reactivate_end_customer('00000000-0000-0000-0000-0000000000a1', 'telegram', 'otro')$$),
  '0', 'con un identificador que no corresponde al pseudónimo no reactiva');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000f2',  -- 13
  $$select count(*)::text from public.reactivate_end_customer('00000000-0000-0000-0000-0000000000a1', 'telegram', 'tg-a1')$$),
  '0', 'con el contexto de otro tenant no reactiva');
select is((select deleted_at is not null from public.end_customers where id = '00000000-0000-0000-0000-0000000000a1'),  -- 14
  true, 'la ficha sigue dormida tras los intentos rechazados');

select * from finish();
rollback;
