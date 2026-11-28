-- Servicio enviador (§6.4): consultar_entregas_outbox (lo ya registrado por canal, con el contexto de tenant) y el
-- cierre `sent` de las filas que agotan los reclamos con algún canal entregado (claim_notification_outbox). Para las
-- filas sin entregas el reclamo sigue dejándolas `failed`.
begin;
select plan(11);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['enviador', 'scheduler_job', 'bot_service'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql (un valor) como p_rol con el tenant p_ctx ('' = sin contexto). '<null>' si es nulo; 'err <sqlstate>'.
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
    return 'err ' || sqlstate;
  end;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-000000007601', 'A', 'entregas-a'),
  ('00000000-0000-0000-0000-000000007602', 'B', 'entregas-b');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000076c1', '00000000-0000-0000-0000-000000007601', 'telegram', 'chat-a'),
  ('00000000-0000-0000-0000-0000000076c2', '00000000-0000-0000-0000-000000007602', 'telegram', 'chat-b');

-- a1: con un canal entregado y otro fallido, en su último reclamo; a2: solo fallidos, en su último reclamo;
-- a3: sin entregas, en su último reclamo; a4: con un canal entregado pero con reclamos disponibles; b1: de otro negocio
insert into public.notification_outbox (id, tenant_id, end_customer_id, channel, template, attempts, entregas,
                                        next_attempt_at) values
  ('00000000-0000-0000-0000-0000000076a1', '00000000-0000-0000-0000-000000007601',
   '00000000-0000-0000-0000-0000000076c1', 'telegram', 'turno_confirmado', 5,
   '{"cc:tg": {"estado": "sent", "error": null, "at": "2026-01-01T00:00:00+00:00"},
     "cc:mail": {"estado": "failed", "error": "http_422", "at": "2026-01-01T00:00:00+00:00"}}',
   now() - interval '1 minute'),
  ('00000000-0000-0000-0000-0000000076a2', '00000000-0000-0000-0000-000000007601',
   '00000000-0000-0000-0000-0000000076c1', 'telegram', 'turno_confirmado', 5,
   '{"cc:tg": {"estado": "failed", "error": "http_503", "at": "2026-01-01T00:00:00+00:00"}}',
   now() - interval '1 minute'),
  ('00000000-0000-0000-0000-0000000076a3', '00000000-0000-0000-0000-000000007601',
   '00000000-0000-0000-0000-0000000076c1', 'telegram', 'turno_confirmado', 5, '{}', now() - interval '1 minute'),
  ('00000000-0000-0000-0000-0000000076a4', '00000000-0000-0000-0000-000000007601',
   '00000000-0000-0000-0000-0000000076c1', 'telegram', 'turno_confirmado', 2,
   '{"cc:tg": {"estado": "sent", "error": null, "at": "2026-01-01T00:00:00+00:00"}}',
   now() + interval '1 hour'),
  ('00000000-0000-0000-0000-0000000076b1', '00000000-0000-0000-0000-000000007602',
   '00000000-0000-0000-0000-0000000076c2', 'telegram', 'turno_confirmado', 1,
   '{"cc:tg": {"estado": "sent", "error": null, "at": "2026-01-01T00:00:00+00:00"}}',
   now() + interval '1 hour');

-- consultar_entregas_outbox
select is(pg_temp.como('enviador', '00000000-0000-0000-0000-000000007601',
  $$select string_agg(clave || ':' || estado || ':' || coalesce(error, '-'), ',' order by clave)
      from public.consultar_entregas_outbox('00000000-0000-0000-0000-0000000076a1')$$),
  'cc:mail:failed:http_422,cc:tg:sent:-', 'devuelve lo registrado por canal, con su código de error');  -- 1
select is(pg_temp.como('enviador', '00000000-0000-0000-0000-000000007601',
  $$select count(*) from public.consultar_entregas_outbox('00000000-0000-0000-0000-0000000076b1')$$),
  '0', 'con el contexto de A no ve las entregas de una fila de B');  -- 2
select is(pg_temp.como('enviador', '',
  $$select count(*) from public.consultar_entregas_outbox('00000000-0000-0000-0000-0000000076a1')$$),
  'err 42501', 'sin contexto de tenant no devuelve nada');  -- 3
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-000000007601',
  $$select count(*) from public.consultar_entregas_outbox('00000000-0000-0000-0000-0000000076a1')$$),
  'err 42501', 'scheduler_job no la ejecuta');  -- 4
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-000000007601',
  $$select count(*) from public.consultar_entregas_outbox('00000000-0000-0000-0000-0000000076a1')$$),
  'err 42501', 'bot_service no la ejecuta');  -- 5

-- claim_notification_outbox: el cierre de las filas que agotaron los reclamos
select ok(pg_temp.como('enviador', '', $$select count(*) from public.claim_notification_outbox(50, 300, 5)$$) ~ '^[0-9]+$',
  'el enviador reclama sin contexto de tenant');  -- 6
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-0000000076a1'), 'sent',
  'agotó los reclamos con un canal entregado: queda sent');  -- 7
select is((select last_error from public.notification_outbox where id = '00000000-0000-0000-0000-0000000076a1'),
  'intentos agotados con entrega parcial', 'y dice por qué');  -- 8
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-0000000076a2'), 'failed',
  'agotó los reclamos con solo canales fallidos: queda failed');  -- 9
select is((select last_error from public.notification_outbox where id = '00000000-0000-0000-0000-0000000076a3'),
  'intentos agotados sin confirmacion de envio', 'agotó los reclamos sin entregas: el mismo dead-letter de siempre');  -- 10
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-0000000076a4'), 'pending',
  'con reclamos disponibles y un canal entregado sigue pending');  -- 11

select * from finish();
rollback;
