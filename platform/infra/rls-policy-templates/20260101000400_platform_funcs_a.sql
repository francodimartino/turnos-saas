-- Migración de plataforma 4: Auth Hook, auditoría, resolución de tenant del webhook,
-- integraciones, Vault y funciones del ciclo de vida del end_customer que ya no dependen del dominio.
-- Ver ARCHITECTURE.md §2.3, §2.6, §2.10, §2.11.5, §3.2, §3.4, §3.5 y §4.2.
--
-- Funciones (todas con `set search_path = ''` y nombres calificados; las que acceden a datos con
-- permisos del dueño son SECURITY DEFINER):
--   custom_access_token_hook      Auth Hook de Supabase: claims tenant_id / is_owner / is_platform_admin.
--   write_audit_log               única vía de escritura de audit_log; el actor sale de session_user.
--   resolve_inbound_tenant        tenant de un webhook entrante, solo tenants 'active'.
--   find_integration              id de la integración del tenant del contexto.
--   get_integration_secret        secret de Vault de una integración del tenant del contexto.
--   store_tenant_integration      alta/actualización de una integración de tenant (solo platform_admin).
--   store_professional_integration / delete_professional_integration
--                                 OAuth de un profesional (Google Calendar) vía función, sin escritura
--                                 directa de dashboard_service a tenant_integrations.
--   pseudonymize_channel_user     HMAC del identificador de canal con la clave de Vault (sin EXECUTE
--                                 para ningún rol de aplicación).
--   find_dormant_end_customer     fila anonimizada más reciente de un identificador, solo del tenant
--                                 del contexto.
--   audit_actor_type, audit_log_append   auxiliares internos de write_audit_log (sin EXECUTE de aplicación).
--
-- Clave HMAC: se siembra una vez por proyecto, fuera del repo, como secret de Vault con el nombre
-- 'channel_user_hmac_key' (el script de setup inicial, §2.10). Sin ella, pseudonymize_channel_user
-- devuelve NULL y la anonimización falla cerrada. No se versiona ningún valor.
--
-- EXECUTE: toda función de esta migración revoca de PUBLIC, anon, authenticated y service_role y se
-- otorga solo a los roles de §3.2. Los default privileges de funciones del rol de migraciones
-- también se revocan, para que las funciones futuras partan sin EXECUTE.

-- ---------------------------------------------------------------------------
-- Default privileges de funciones: ninguna función nueva nace ejecutable por nadie
-- ---------------------------------------------------------------------------
alter default privileges in schema public
  revoke execute on functions from public, anon, authenticated, service_role;
alter default privileges
  revoke execute on functions from public, anon, authenticated, service_role;

-- El Auth Hook corre como supabase_auth_admin: necesita USAGE para resolver public.custom_access_token_hook
grant usage on schema public to supabase_auth_admin;

-- ---------------------------------------------------------------------------
-- Auth Hook (§3.4)
-- jsonb_build_object convierte el NULL de SQL en un null de JSON: con jsonb_set(..., to_jsonb(NULL))
-- el resultado entero sería NULL y el token saldría roto. platform_auth trata un tenant_id null como
-- sesión no autorizada para dashboard-backend (staff desactivado); admin-backend exige is_platform_admin.
-- ---------------------------------------------------------------------------
create function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_user uuid := (event ->> 'user_id')::uuid;
  v_tenant_id uuid;
  v_is_owner boolean;
begin
  select tu.tenant_id, tu.is_owner
    into v_tenant_id, v_is_owner
    from public.tenant_users tu
   where tu.auth_user_id = v_user and tu.is_active;

  return jsonb_set(
    event,
    '{claims}',
    coalesce(event -> 'claims', '{}'::jsonb) || jsonb_build_object(
      'tenant_id', v_tenant_id,
      'is_owner', coalesce(v_is_owner, false),
      'is_platform_admin', exists (select 1 from public.platform_admins pa where pa.auth_user_id = v_user)
    )
  );
end
$$;

revoke all on function public.custom_access_token_hook(jsonb) from public, anon, authenticated, service_role;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;

-- ---------------------------------------------------------------------------
-- Auditoría (§2.10, §3.2)
-- ---------------------------------------------------------------------------

-- Mapeo rol de login -> actor_type. Pura: se prueba sin conexión real. Roles no listados -> NULL.
create function public.audit_actor_type(p_session_user text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case p_session_user
    when 'bot_service' then 'bot'
    when 'dashboard_service' then 'staff'
    when 'platform_admin' then 'platform_admin'
    when 'purge_job' then 'system'
  end
$$;

revoke all on function public.audit_actor_type(text) from public, anon, authenticated, service_role;

-- Inserción interna: las funciones definer de esta migración la usan con el actor ya decidido.
create function public.audit_log_append(
  p_tenant_id uuid,
  p_actor_type text,
  p_actor_id uuid,
  p_action text,
  p_entity text,
  p_entity_id uuid,
  p_metadata jsonb
)
returns uuid
language sql
security definer
set search_path = ''
as $$
  insert into public.audit_log (tenant_id, actor_type, actor_id, action, entity, entity_id, metadata)
  values (p_tenant_id, p_actor_type, p_actor_id, p_action, p_entity, p_entity_id, p_metadata)
  returning id
$$;

revoke all on function public.audit_log_append(uuid, text, uuid, text, text, uuid, jsonb)
  from public, anon, authenticated, service_role;

-- Única vía de escritura de audit_log. El actor NUNCA es un parámetro: se deriva de session_user
-- (dentro de una función SECURITY DEFINER, current_user es el dueño). actor_id sale de
-- app.current_actor_id, que platform_tenancy setea desde la identidad ya verificada.
-- bot_service/dashboard_service solo escriben en el tenant del contexto; platform_admin y purge_job
-- (system) pueden indicar tenant o dejarlo sin tenant (acciones globales).
create function public.write_audit_log(
  p_action text,
  p_entity text default null,
  p_entity_id uuid default null,
  p_metadata jsonb default null,
  p_tenant_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor_type text := public.audit_actor_type(session_user);
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_tenant_id uuid;
begin
  if v_actor_type is null then
    raise exception 'rol no autorizado para escribir en audit_log: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  if p_action is null or btrim(p_action) = '' then
    raise exception 'write_audit_log: action es obligatoria'
      using errcode = 'invalid_parameter_value';
  end if;

  if v_actor_type in ('platform_admin', 'system') then
    v_tenant_id := coalesce(p_tenant_id, v_ctx);
  else
    if v_ctx is null then
      raise exception 'contexto de tenant requerido para escribir en audit_log'
        using errcode = 'insufficient_privilege';
    end if;
    if p_tenant_id is not null and p_tenant_id <> v_ctx then
      raise exception 'no se puede escribir en el audit_log de otro tenant'
        using errcode = 'insufficient_privilege';
    end if;
    v_tenant_id := v_ctx;
  end if;

  return public.audit_log_append(v_tenant_id, v_actor_type, v_actor_id, p_action, p_entity, p_entity_id, p_metadata);
end
$$;

revoke all on function public.write_audit_log(text, text, uuid, jsonb, uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.write_audit_log(text, text, uuid, jsonb, uuid)
  to bot_service, dashboard_service, platform_admin, purge_job;

-- ---------------------------------------------------------------------------
-- Resolución del tenant del webhook entrante (§2.6)
-- Previa al contexto de tenant: se resuelve por un identificador NO secreto de tenant_integrations.config.
-- Devuelve solo si el tenant está 'active'; si no hay fila, el webhook responde 200 y descarta.
-- ---------------------------------------------------------------------------
create function public.resolve_inbound_tenant(p_channel text, p_external_key text)
returns table (tenant_id uuid, integration_id uuid)
language sql
stable
security definer
set search_path = ''
as $$
  select ti.tenant_id, ti.id
    from public.tenant_integrations ti
    join public.tenants t on t.id = ti.tenant_id
   where t.status = 'active'
     and p_external_key is not null
     and ti.professional_id is null
     and (
       (p_channel = 'telegram' and ti.integration_type = 'telegram_bot'
         and ti.config ->> 'webhook_key' = p_external_key)
       or
       (p_channel = 'whatsapp' and ti.integration_type = 'whatsapp_business'
         and ti.config ->> 'phone_number_id' = p_external_key)
     )
$$;

revoke all on function public.resolve_inbound_tenant(text, text) from public, anon, authenticated, service_role;
grant execute on function public.resolve_inbound_tenant(text, text) to bot_service;

-- ---------------------------------------------------------------------------
-- Integraciones del tenant del contexto (§2.6, §3.2)
-- ---------------------------------------------------------------------------
create function public.find_integration(p_integration_type text, p_professional_id uuid default null)
returns uuid
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_id uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  select ti.id
    into v_id
    from public.tenant_integrations ti
   where ti.tenant_id = v_ctx
     and ti.integration_type = p_integration_type
     and ti.professional_id is not distinct from p_professional_id;

  return v_id;
end
$$;

revoke all on function public.find_integration(text, uuid) from public, anon, authenticated, service_role;
grant execute on function public.find_integration(text, uuid) to bot_service, dashboard_service, scheduler_job;

-- Devuelve el secret descifrado de Vault. Exige contexto de tenant y que la integración sea de ese tenant.
-- Inexistente y ajena dan el mismo error, para no revelar qué ids existen.
create function public.get_integration_secret(p_integration_id uuid)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_tenant_id uuid;
  v_secret_id uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  select ti.tenant_id, ti.secret_id
    into v_tenant_id, v_secret_id
    from public.tenant_integrations ti
   where ti.id = p_integration_id;

  if not found or v_tenant_id <> v_ctx then
    raise exception 'integración inexistente o de otro tenant' using errcode = 'insufficient_privilege';
  end if;

  if v_secret_id is null then
    return null;
  end if;

  return (select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = v_secret_id);
end
$$;

revoke all on function public.get_integration_secret(uuid) from public, anon, authenticated, service_role;
grant execute on function public.get_integration_secret(uuid)
  to bot_service, dashboard_service, platform_admin, purge_job, scheduler_job;

-- ---------------------------------------------------------------------------
-- Alta de una integración de tenant (§2.11.5): solo platform_admin
-- El secret se guarda en Vault; tenant_integrations solo referencia secret_id. Además del EXECUTE,
-- verifica el caller real (session_user): un SET ROLE a platform_admin no alcanza.
-- Las integraciones por-profesional (google_calendar) no entran acá: van por store_professional_integration.
-- ---------------------------------------------------------------------------
create function public.store_tenant_integration(
  p_tenant_id uuid,
  p_integration_type text,
  p_config jsonb,
  p_secret text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_config jsonb := coalesce(p_config, '{}'::jsonb);
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_id uuid;
  v_secret_id uuid;
begin
  if session_user <> 'platform_admin' then
    raise exception 'rol no autorizado para guardar integraciones: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  if p_integration_type is null or p_integration_type not in ('telegram_bot', 'whatsapp_business', 'mercadopago') then
    raise exception 'tipo de integración de tenant no soportado'
      using errcode = 'invalid_parameter_value';
  end if;
  if p_secret is null or p_secret = '' then
    raise exception 'el secret es obligatorio'
      using errcode = 'invalid_parameter_value';
  end if;
  if jsonb_typeof(v_config) <> 'object' then
    raise exception 'config debe ser un objeto JSON'
      using errcode = 'invalid_parameter_value';
  end if;

  perform 1 from public.tenants t where t.id = p_tenant_id for share;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'foreign_key_violation';
  end if;

  select ti.id, ti.secret_id
    into v_id, v_secret_id
    from public.tenant_integrations ti
   where ti.tenant_id = p_tenant_id
     and ti.integration_type = p_integration_type
     and ti.professional_id is null
   for update;

  if found then
    if v_secret_id is null then
      v_secret_id := vault.create_secret(
        p_secret, 'tenant_integration:' || gen_random_uuid()::text, p_integration_type || ' del tenant');
    else
      perform vault.update_secret(v_secret_id, p_secret);
    end if;
    update public.tenant_integrations
       set secret_id = v_secret_id, config = v_config
     where id = v_id;
  else
    v_id := gen_random_uuid();
    v_secret_id := vault.create_secret(
      p_secret, 'tenant_integration:' || gen_random_uuid()::text, p_integration_type || ' del tenant');
    insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id, secret_id, config)
    values (v_id, p_tenant_id, p_integration_type, null, v_secret_id, v_config);
  end if;

  perform public.audit_log_append(
    p_tenant_id, 'platform_admin', v_actor_id, 'store_tenant_integration', 'tenant_integrations', v_id,
    jsonb_build_object('integration_type', p_integration_type));

  return v_id;
end
$$;

revoke all on function public.store_tenant_integration(uuid, text, jsonb, text)
  from public, anon, authenticated, service_role;
grant execute on function public.store_tenant_integration(uuid, text, jsonb, text) to platform_admin;

-- ---------------------------------------------------------------------------
-- OAuth de un profesional (§3.2, dashboard_service): sin escritura directa a tenant_integrations
-- Solo google_calendar, solo profesionales activos del tenant del contexto.
-- ---------------------------------------------------------------------------
create function public.store_professional_integration(
  p_integration_type text,
  p_professional_id uuid,
  p_config jsonb,
  p_secret text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_config jsonb := coalesce(p_config, '{}'::jsonb);
  v_id uuid;
  v_secret_id uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;
  if p_integration_type is distinct from 'google_calendar' then
    raise exception 'solo se admiten integraciones por-profesional de tipo google_calendar'
      using errcode = 'invalid_parameter_value';
  end if;
  if p_secret is null or p_secret = '' then
    raise exception 'el secret es obligatorio' using errcode = 'invalid_parameter_value';
  end if;
  if jsonb_typeof(v_config) <> 'object' then
    raise exception 'config debe ser un objeto JSON' using errcode = 'invalid_parameter_value';
  end if;

  perform 1
    from public.professionals p
   where p.id = p_professional_id and p.tenant_id = v_ctx and p.is_active
   for share;
  if not found then
    raise exception 'profesional inexistente, inactivo o de otro tenant' using errcode = 'insufficient_privilege';
  end if;

  select ti.id, ti.secret_id
    into v_id, v_secret_id
    from public.tenant_integrations ti
   where ti.tenant_id = v_ctx
     and ti.integration_type = p_integration_type
     and ti.professional_id = p_professional_id
   for update;

  if found then
    if v_secret_id is null then
      v_secret_id := vault.create_secret(
        p_secret, 'tenant_integration:' || gen_random_uuid()::text, p_integration_type || ' de un profesional');
    else
      perform vault.update_secret(v_secret_id, p_secret);
    end if;
    update public.tenant_integrations
       set secret_id = v_secret_id, config = v_config
     where id = v_id;
  else
    v_id := gen_random_uuid();
    v_secret_id := vault.create_secret(
      p_secret, 'tenant_integration:' || gen_random_uuid()::text, p_integration_type || ' de un profesional');
    insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id, secret_id, config)
    values (v_id, v_ctx, p_integration_type, p_professional_id, v_secret_id, v_config);
  end if;

  perform public.audit_log_append(
    v_ctx, 'staff', v_actor_id, 'connect_google_calendar', 'tenant_integrations', v_id,
    jsonb_build_object('professional_id', p_professional_id));

  return v_id;
end
$$;

revoke all on function public.store_professional_integration(text, uuid, jsonb, text)
  from public, anon, authenticated, service_role;
grant execute on function public.store_professional_integration(text, uuid, jsonb, text) to dashboard_service;

-- Desconectar Google Calendar de un profesional: borra la integración y su secret de Vault.
-- Devuelve false si el tenant del contexto no tiene esa conexión.
create function public.delete_professional_integration(p_professional_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_id uuid;
  v_secret_id uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  delete from public.tenant_integrations ti
   where ti.tenant_id = v_ctx
     and ti.integration_type = 'google_calendar'
     and ti.professional_id = p_professional_id
  returning ti.id, ti.secret_id into v_id, v_secret_id;

  if not found then
    return false;
  end if;

  if v_secret_id is not null then
    delete from vault.secrets s where s.id = v_secret_id;
  end if;

  perform public.audit_log_append(
    v_ctx, 'staff', v_actor_id, 'disconnect_google_calendar', 'tenant_integrations', v_id,
    jsonb_build_object('professional_id', p_professional_id));

  return true;
end
$$;

revoke all on function public.delete_professional_integration(uuid) from public, anon, authenticated, service_role;
grant execute on function public.delete_professional_integration(uuid) to dashboard_service;

-- ---------------------------------------------------------------------------
-- Seudonimización del identificador de canal (§2.3, §3.5)
-- HMAC-SHA256(clave, tenant_id | channel | channel_user_id); incluir tenant_id impide vincular a la
-- misma persona entre tenants. La clave vive en Vault y nunca llega a la aplicación: sin EXECUTE para
-- ningún rol de aplicación, solo la usan otras funciones definer. Sin clave -> NULL (falla cerrada).
-- ---------------------------------------------------------------------------
create function public.pseudonymize_channel_user(p_tenant_id uuid, p_channel text, p_channel_user_id text)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_key text;
begin
  if p_tenant_id is null or p_channel is null or p_channel_user_id is null then
    return null;
  end if;

  select ds.decrypted_secret
    into v_key
    from vault.decrypted_secrets ds
   where ds.name = 'channel_user_hmac_key'
   order by ds.created_at desc
   limit 1;

  if v_key is null or v_key = '' then
    return null;
  end if;

  return encode(
    extensions.hmac(p_tenant_id::text || '|' || p_channel || '|' || p_channel_user_id, v_key, 'sha256'),
    'hex');
end
$$;

revoke all on function public.pseudonymize_channel_user(uuid, text, text)
  from public, anon, authenticated, service_role;

-- Recontacto (§2.3): fila anonimizada (dormida) más reciente de un identificador, solo del tenant del
-- contexto. Sin la clave en Vault no hay recontacto (no devuelve nada).
create function public.find_dormant_end_customer(p_channel text, p_channel_user_id text)
returns setof public.end_customers
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_hash text;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  v_hash := public.pseudonymize_channel_user(v_ctx, p_channel, p_channel_user_id);
  if v_hash is null then
    return;
  end if;

  return query
    select ec.*
      from public.end_customers ec
     where ec.tenant_id = v_ctx
       and ec.channel = p_channel
       and ec.channel_user_hash = v_hash
       and ec.channel_user_id is null
       and ec.deleted_at is not null
     order by ec.deleted_at desc
     limit 1;
end
$$;

revoke all on function public.find_dormant_end_customer(text, text) from public, anon, authenticated, service_role;
grant execute on function public.find_dormant_end_customer(text, text) to bot_service;
