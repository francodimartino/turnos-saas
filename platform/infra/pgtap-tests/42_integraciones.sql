-- Integraciones (§2.6, §2.11.5, §3.2): find_integration, get_integration_secret,
-- store_tenant_integration (solo platform_admin) y el alta/baja del OAuth de un profesional
-- vía función definer (dashboard_service, sin escritura directa a tenant_integrations).
--
-- store_tenant_integration además exige session_user = 'platform_admin' (el caller real): su camino
-- exitoso se prueba con una conexión real en tests/integracion/test_funciones_plataforma.py.
begin;
select plan(55);

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

-- Ejecuta un select escalar como p_rol con el contexto de tenant p_ctx (null = sin contexto) y
-- devuelve el valor como texto, '<null>' o 'err <sqlstate> <mensaje>'.
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

-- Ejecuta una sentencia como p_rol y revierte sus efectos; devuelve 'ok' o 'err <sqlstate> <mensaje>'.
create function pg_temp.sin_efectos(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    execute p_sql;
    raise exception 'revertir' using errcode = 'ZZ001';
  exception when others then
    reset role;
    if sqlstate = 'ZZ001' then
      return 'ok';
    end if;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'int-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'int-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', 'Prof B');

do $$
begin
  perform vault.create_secret('{"bot_token": "tok-a", "webhook_secret": "ws-a"}', 'int-test-secret-a');
  perform vault.create_secret('{"bot_token": "tok-b", "webhook_secret": "ws-b"}', 'int-test-secret-b');
  perform vault.create_secret('cred-prof-a', 'int-test-secret-prof-a');
end
$$;

insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id, secret_id, config) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'telegram_bot', null,
    (select id from vault.secrets where name = 'int-test-secret-a'), '{"webhook_key": "int-k-a"}'),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000a2', 'telegram_bot', null,
    (select id from vault.secrets where name = 'int-test-secret-b'), '{"webhook_key": "int-k-b"}'),
  ('00000000-0000-0000-0000-0000000000f3', '00000000-0000-0000-0000-0000000000a1', 'google_calendar',
    '00000000-0000-0000-0000-0000000000b1', (select id from vault.secrets where name = 'int-test-secret-prof-a'), '{}');

-- ---------------------------------------------------------------------------
-- find_integration(integration_type, professional_id)
-- ---------------------------------------------------------------------------
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('telegram_bot')::text$$),
  '00000000-0000-0000-0000-0000000000f1', 'find_integration devuelve la integración del tenant del contexto (A)');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a2',
  $$select public.find_integration('telegram_bot')::text$$),
  '00000000-0000-0000-0000-0000000000f2', 'con contexto B devuelve la de B, nunca la de A');
select ok(pg_temp.como('bot_service', null, $$select public.find_integration('telegram_bot')::text$$)
  like 'err 42501%contexto de tenant%', 'find_integration sin contexto de tenant: rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('whatsapp_business')::text$$),
  '<null>', 'find_integration de un tipo que el tenant no tiene: null');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1')::text$$),
  '00000000-0000-0000-0000-0000000000f3', 'find_integration por profesional (dashboard_service)');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a2',
  $$select public.find_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1')::text$$),
  '<null>', 'el profesional de A no se encuentra desde el contexto de B');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('google_calendar')::text$$),
  '<null>', 'sin professional_id solo se buscan integraciones de tenant (professional_id null)');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('telegram_bot')::text$$),
  '00000000-0000-0000-0000-0000000000f1', 'scheduler_job puede ejecutar find_integration');
select ok(pg_temp.como('platform_admin', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('telegram_bot')::text$$) like 'err 42501%',
  'platform_admin no ejecuta find_integration');
select ok(pg_temp.como('platform_admin_readonly', '00000000-0000-0000-0000-0000000000a1',
  $$select public.find_integration('telegram_bot')::text$$) like 'err 42501%',
  'platform_admin_readonly no ejecuta find_integration');

-- ---------------------------------------------------------------------------
-- get_integration_secret(integration_id)
-- ---------------------------------------------------------------------------
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$),
  '{"bot_token": "tok-a", "webhook_secret": "ws-a"}', 'bot_service obtiene el secret de la integración de su tenant');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f2')$$)
  like 'err 42501%otro tenant%', 'rechaza la integración de otro tenant (B desde A)');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a2',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$)
  like 'err 42501%otro tenant%', 'rechaza la integración de otro tenant (A desde B)');
select ok(pg_temp.como('bot_service', null,
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$)
  like 'err 42501%contexto de tenant%', 'sin contexto de tenant: rechaza');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-00000000dead')$$)
  like 'err 42501%', 'integración inexistente: mismo rechazo (no revela qué ids existen)');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret(null)$$) like 'err 42501%', 'id nulo: rechaza');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f3')$$),
  'cred-prof-a', 'dashboard_service obtiene el secret de una integración de su tenant');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$),
  '{"bot_token": "tok-a", "webhook_secret": "ws-a"}', 'platform_admin con contexto del tenant: puede');
select ok(pg_temp.como('platform_admin', null,
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$)
  like 'err 42501%contexto de tenant%', 'platform_admin sin contexto: rechaza (también exige el tenant del contexto)');
select is(pg_temp.como('purge_job', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f3')$$),
  'cred-prof-a', 'purge_job con el contexto del tenant que purga: puede');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$),
  '{"bot_token": "tok-a", "webhook_secret": "ws-a"}', 'scheduler_job con contexto: puede');
select ok(pg_temp.como('platform_admin_readonly', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f1')$$)
  like 'err 42501%permission denied%', 'platform_admin_readonly no ejecuta get_integration_secret');

update public.tenant_integrations set secret_id = null where id = '00000000-0000-0000-0000-0000000000f3';
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.get_integration_secret('00000000-0000-0000-0000-0000000000f3')$$),
  '<null>', 'integración sin secret (conexión dada de baja): devuelve null');

-- ---------------------------------------------------------------------------
-- store_tenant_integration: solo platform_admin
-- ---------------------------------------------------------------------------
select ok(pg_temp.sin_efectos(r, '00000000-0000-0000-0000-0000000000a1',
    $$select public.store_tenant_integration('00000000-0000-0000-0000-0000000000a1', 'whatsapp_business', '{"phone_number_id": "x"}', 'secreto')$$)
  like 'err 42501%permission denied%', 'store_tenant_integration: ' || r || ' no puede ejecutarla')
  from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly', 'purge_job', 'scheduler_job']) r;

select ok(pg_temp.sin_efectos('platform_admin', null,
    $$select public.store_tenant_integration('00000000-0000-0000-0000-0000000000a1', 'whatsapp_business', '{"phone_number_id": "x"}', 'secreto')$$)
  like 'err 42501%rol no autorizado%',
  'store_tenant_integration verifica además el caller real (session_user): un SET ROLE no alcanza');
select ok(pg_temp.sin_efectos('platform_admin', null,
    $$select public.store_tenant_integration('00000000-0000-0000-0000-0000000000a1', 'whatsapp_business', '{"phone_number_id": "x"}', 'secreto')$$)
  not like '%secreto%', 'el mensaje de error no incluye el secret');
select ok(not has_function_privilege('anon', 'public.store_tenant_integration(uuid, text, jsonb, text)', 'execute')
      and not has_function_privilege('authenticated', 'public.store_tenant_integration(uuid, text, jsonb, text)', 'execute')
      and not has_function_privilege('service_role', 'public.store_tenant_integration(uuid, text, jsonb, text)', 'execute'),
  'anon/authenticated/service_role no ejecutan store_tenant_integration');
select ok(has_function_privilege('platform_admin', 'public.store_tenant_integration(uuid, text, jsonb, text)', 'execute'),
  'platform_admin tiene EXECUTE sobre store_tenant_integration');

-- el secret nunca queda en una columna en claro: tenant_integrations solo referencia secret_id
select is_empty(
  $$select column_name from information_schema.columns
     where table_schema = 'public' and table_name = 'tenant_integrations'
       and (column_name ilike '%secret%' or column_name ilike '%token%' or column_name ilike '%password%'
            or column_name ilike '%credential%')
       and not (column_name = 'secret_id' and data_type = 'uuid')$$,
  'tenant_integrations no tiene ninguna columna de credencial en claro (solo secret_id uuid)');

-- ---------------------------------------------------------------------------
-- OAuth de un profesional vía función definer (dashboard_service)
-- ---------------------------------------------------------------------------
select ok(pg_temp.sin_efectos(r, '00000000-0000-0000-0000-0000000000a1',
    $$select public.store_professional_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1', '{}', 'oauth')$$)
  like 'err 42501%permission denied%', 'store_professional_integration: ' || r || ' no puede ejecutarla')
  from unnest(array['bot_service', 'platform_admin', 'platform_admin_readonly', 'purge_job', 'scheduler_job']) r;

select ok(pg_temp.como('dashboard_service', null,
  $$select public.store_professional_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1', '{}', 'oauth')::text$$)
  like 'err 42501%contexto de tenant%', 'store_professional_integration sin contexto de tenant: rechaza');
select ok(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.store_professional_integration('google_calendar', '00000000-0000-0000-0000-0000000000b2', '{}', 'oauth')::text$$)
  like 'err 42501%profesional%', 'rechaza un profesional de otro tenant');
select ok(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.store_professional_integration('telegram_bot', '00000000-0000-0000-0000-0000000000b1', '{}', 'oauth')::text$$)
  like 'err 22023%', 'solo acepta integraciones por-profesional (google_calendar)');

-- alta, actualización (mismo registro) y baja
create temp table alta as
  select pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
    $$select public.store_professional_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1', '{"calendar_id": "c1"}', 'oauth-v1')::text$$) as id;

select ok((select id from alta) ~ '^[0-9a-f-]{36}$', 'store_professional_integration devuelve el id de la integración');
select is((select secret_id is not null from public.tenant_integrations where id::text = (select id from alta)),
  true, 'la integración guarda una referencia secret_id');
select is((select ds.decrypted_secret from public.tenant_integrations ti
             join vault.decrypted_secrets ds on ds.id = ti.secret_id where ti.id::text = (select id from alta)),
  'oauth-v1', 'el secret quedó en Vault');
select is((select config from public.tenant_integrations where id::text = (select id from alta)),
  '{"calendar_id": "c1"}'::jsonb, 'config no secreta guardada');
select is((select count(*) from public.tenant_integrations
            where tenant_id = '00000000-0000-0000-0000-0000000000a1' and integration_type = 'google_calendar'
              and professional_id = '00000000-0000-0000-0000-0000000000b1'),
  1::bigint, 'una sola integración google_calendar por profesional');
select is((select to_jsonb(ti)::text like '%oauth-v1%' from public.tenant_integrations ti where id::text = (select id from alta)),
  false, 'el secret no aparece en ninguna columna de la fila');

select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.store_professional_integration('google_calendar', '00000000-0000-0000-0000-0000000000b1', '{"calendar_id": "c2"}', 'oauth-v2')::text$$),
  (select id from alta), 'reconectar actualiza la misma integración');
select is((select ds.decrypted_secret from public.tenant_integrations ti
             join vault.decrypted_secrets ds on ds.id = ti.secret_id where ti.id::text = (select id from alta)),
  'oauth-v2', 'la reconexión reemplaza el secret en Vault');
select is((select count(*) from public.audit_log where action = 'connect_google_calendar'
            and tenant_id = '00000000-0000-0000-0000-0000000000a1' and actor_type = 'staff'),
  2::bigint, 'cada conexión deja registro en audit_log (actor staff, sin el secret)');
select is((select count(*) from public.audit_log where metadata::text like '%oauth-v%'),
  0::bigint, 'audit_log no contiene el secret');

select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a2',
  $$select public.delete_professional_integration('00000000-0000-0000-0000-0000000000b1')::text$$),
  'false', 'desconectar desde otro tenant no encuentra nada ni borra');
select is((select count(*) from public.tenant_integrations where id::text = (select id from alta)),
  1::bigint, 'la integración sigue ahí tras el intento cruzado');

create temp table secreto_previo as
  select secret_id from public.tenant_integrations where id::text = (select id from alta);
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.delete_professional_integration('00000000-0000-0000-0000-0000000000b1')::text$$),
  'true', 'desconectar elimina la integración del profesional');
select is((select count(*) from vault.secrets where id = (select secret_id from secreto_previo)),
  0::bigint, 'y elimina el secret de Vault');

select * from finish();
rollback;
