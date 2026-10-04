-- resolve_inbound_tenant (§2.6): resuelve el tenant de un webhook por un identificador no secreto
-- y solo para tenants 'active'.
begin;
select plan(15);

-- El rol de migraciones (postgres) no es superusuario en Supabase: para actuar como cada rol de
-- login usa SET ROLE, con membresía otorgada dentro de esta transacción (se revierte con el rollback).
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

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'activo-tg', 'rit-1', 'active', null),
  ('00000000-0000-0000-0000-0000000000a2', 'activo-wa', 'rit-2', 'active', null),
  ('00000000-0000-0000-0000-0000000000a3', 'suspendido', 'rit-3', 'suspended', null),
  ('00000000-0000-0000-0000-0000000000a4', 'inactivo', 'rit-4', 'inactive', now()),
  ('00000000-0000-0000-0000-0000000000a5', 'purgado', 'rit-5', 'purged', null);
insert into public.tenant_integrations (id, tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'telegram_bot', '{"webhook_key": "rit-tg-1"}'),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000a2', 'whatsapp_business', '{"phone_number_id": "rit-wa-2"}'),
  ('00000000-0000-0000-0000-0000000000f3', '00000000-0000-0000-0000-0000000000a3', 'telegram_bot', '{"webhook_key": "rit-tg-3"}'),
  ('00000000-0000-0000-0000-0000000000f4', '00000000-0000-0000-0000-0000000000a4', 'whatsapp_business', '{"phone_number_id": "rit-wa-4"}'),
  ('00000000-0000-0000-0000-0000000000f5', '00000000-0000-0000-0000-0000000000a5', 'telegram_bot', '{"webhook_key": "rit-tg-5"}');

select is((select tenant_id from public.resolve_inbound_tenant('telegram', 'rit-tg-1')),
  '00000000-0000-0000-0000-0000000000a1'::uuid, 'telegram + webhook_key de un tenant active: devuelve el tenant');
select is((select integration_id from public.resolve_inbound_tenant('telegram', 'rit-tg-1')),
  '00000000-0000-0000-0000-0000000000f1'::uuid, 'devuelve también el id de la integración');
select is((select tenant_id from public.resolve_inbound_tenant('whatsapp', 'rit-wa-2')),
  '00000000-0000-0000-0000-0000000000a2'::uuid, 'whatsapp + phone_number_id de un tenant active: devuelve el tenant');

select is_empty($$select * from public.resolve_inbound_tenant('telegram', 'rit-tg-3')$$,
  'tenant suspended: no se resuelve');
select is_empty($$select * from public.resolve_inbound_tenant('whatsapp', 'rit-wa-4')$$,
  'tenant inactive: no se resuelve');
select is_empty($$select * from public.resolve_inbound_tenant('telegram', 'rit-tg-5')$$,
  'tenant purged: no se resuelve');
select is_empty($$select * from public.resolve_inbound_tenant('telegram', 'no-existe')$$,
  'identificador desconocido: vacío');
select is_empty($$select * from public.resolve_inbound_tenant('whatsapp', 'rit-tg-1')$$,
  'una webhook_key de Telegram no resuelve por el canal whatsapp');
select is_empty($$select * from public.resolve_inbound_tenant('telegram', 'rit-wa-2')$$,
  'un phone_number_id de WhatsApp no resuelve por el canal telegram');
select is_empty($$select * from public.resolve_inbound_tenant('sms', 'rit-tg-1')$$,
  'canal desconocido: vacío');
select is_empty($$select * from public.resolve_inbound_tenant('telegram', null)$$,
  'clave nula: vacío');

-- Solo bot_service ejecuta (§3.2); no requiere contexto de tenant (es previa a él)
select is(pg_temp.como('bot_service', null,
  $$select tenant_id::text from public.resolve_inbound_tenant('telegram', 'rit-tg-1')$$),
  '00000000-0000-0000-0000-0000000000a1', 'bot_service resuelve sin contexto de tenant');
select is_empty(
  $$select r from unnest(array['dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.resolve_inbound_tenant(text, text)', 'execute')$$,
  'ningún otro rol ejecuta resolve_inbound_tenant');
select ok(pg_temp.como('dashboard_service', null,
  $$select tenant_id::text from public.resolve_inbound_tenant('telegram', 'rit-tg-1')$$) like 'err 42501%',
  'dashboard_service: permission denied');
select ok(pg_temp.como('scheduler_job', null,
  $$select tenant_id::text from public.resolve_inbound_tenant('telegram', 'rit-tg-1')$$) like 'err 42501%',
  'scheduler_job: permission denied');

select * from finish();
rollback;
