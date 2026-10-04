-- Migración de plataforma 5: anonimización del end_customer, purga de tenant y limpieza programada.
-- Ver ARCHITECTURE.md §2.2, §2.3, §2.10, §3.2, §3.5, §4.2 y §5.4.1.
--
-- Funciones (todas con `set search_path = ''` y nombres calificados):
--   anonymize_end_customer_core  lógica real de la anonimización; recibe el actor ya decidido. Sin EXECUTE
--                                para ningún rol de aplicación (la usan otras funciones). Corre como invocador:
--                                solo la alcanzan funciones SECURITY DEFINER, con los permisos del dueño.
--                                Las ramas de dominio (payments del pagador, cancelar bookings pendientes)
--                                NO están acá: la migración del producto reemplaza esta función
--                                (create or replace, misma firma) agregándolas; así la purga de tenant,
--                                que la llama directamente, también las ejecuta.
--   anonymize_end_customer       función pública: deriva el actor de session_user y delega en la core.
--   list_tenants_due_for_purge   tenants inactive con el período de gracia vencido y su atraso en días,
--                                calculados en la base desde platform_settings.
--   purge_tenant_data            pasos 1 de la cascada de §2.2: re-valida inactive + período vencido,
--                                anonimiza end_customers, borra sesiones del bot y secrets de Vault y devuelve
--                                los auth_user_id de sus tenant_users. Idempotente.
--   mark_tenant_purged           paso 3: verifica que no queden cuentas, setea purged y libera el slug.
--   prune_inbound_messages       borra los inbound_messages de más de 7 días (§2.10, §5.4.1).
--
-- Por qué la core existe: dentro de una función SECURITY DEFINER, session_user es el rol de login del caller.
-- La función pública lo traduce a actor_type (bot / staff / platform_admin / system); la purga, que ya está
-- autorizada por sus propias precondiciones y por el EXECUTE exclusivo de purge_job, llama a la core con
-- 'system' (el mismo valor que daría session_user = purge_job). Eso permite probar la cascada completa en
-- pgTAP, donde el session_user es el rol de migraciones.
--
-- Fuera de esta migración (no son de plataforma): el nombre de professionals y los eventos de Google Calendar
-- (calendar_scrub_targets) de la purga, y las ramas de dominio de la anonimización. Van en la migración del
-- producto (§4.2).
--
-- EXECUTE: toda función revoca de PUBLIC, anon, authenticated y service_role y se otorga solo a los roles de
-- §3.2 (los default privileges de funciones ya están revocados desde la migración 4).

-- ---------------------------------------------------------------------------
-- Anonimización del end_customer (§2.3, §3.5)
-- ---------------------------------------------------------------------------

-- Lógica de la anonimización. p_actor_type lo decide quien llama (nunca un parámetro de un caller externo).
-- platform_admin y system (purga) cruzan tenants; bot y staff exigen contexto de tenant no nulo y coincidente.
-- Para ellos un end_customer inexistente y uno de otro tenant dan el mismo error (no revela qué ids existen).
create function public.anonymize_end_customer_core(p_id uuid, p_actor_type text, p_actor_id uuid)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_tenant_id uuid;
  v_channel text;
  v_channel_user_id text;
begin
  if p_actor_type is null or p_actor_type not in ('bot', 'staff', 'platform_admin', 'system') then
    raise exception 'actor_type no autorizado para anonimizar' using errcode = 'insufficient_privilege';
  end if;

  if p_actor_type in ('platform_admin', 'system') then
    select ec.tenant_id, ec.channel, ec.channel_user_id
      into v_tenant_id, v_channel, v_channel_user_id
      from public.end_customers ec
     where ec.id = p_id
       for update;
    if not found then
      raise exception 'end_customer inexistente' using errcode = 'no_data_found';
    end if;
  else
    -- NULL <> tenant no dispararía la excepción (NULL se trata como false en un IF): se exige no nulo
    if v_caller_tenant is null then
      raise exception 'tenant mismatch: contexto de tenant requerido' using errcode = 'insufficient_privilege';
    end if;
    select ec.tenant_id, ec.channel, ec.channel_user_id
      into v_tenant_id, v_channel, v_channel_user_id
      from public.end_customers ec
     where ec.id = p_id and ec.tenant_id = v_caller_tenant
       for update;
    if not found then
      raise exception 'tenant mismatch: end_customer inexistente o de otro tenant'
        using errcode = 'insufficient_privilege';
    end if;
  end if;

  -- Idempotente: si channel_user_id ya es null (ya anonimizado) se conserva el hash existente.
  -- Sin la clave HMAC en Vault pseudonymize_channel_user devuelve NULL y el check de end_customers
  -- (canal <=> identificador o hash) hace fallar la operación: falla cerrada, sin dejar la fila a medias.
  update public.end_customers ec
     set phone = null,
         name = null,
         extra_data = '{}'::jsonb,
         deleted_at = coalesce(ec.deleted_at, now()),
         channel_user_hash = case
           when v_channel_user_id is null then ec.channel_user_hash
           else public.pseudonymize_channel_user(v_tenant_id, v_channel, v_channel_user_id)
         end,
         channel_user_id = null
   where ec.id = p_id;

  -- copias de PII fuera de end_customers (§2.10)
  update public.notifications_log nl
     set last_error = null
   where nl.end_customer_id = p_id and nl.tenant_id = v_tenant_id;

  delete from public.bot_sessions bs
   where bs.tenant_id = v_tenant_id
     and bs.channel = v_channel
     and bs.channel_user_id = v_channel_user_id;

  -- constancia del borrado: fecha, actor y campos, sin retener ningún valor original
  perform public.audit_log_append(
    v_tenant_id, p_actor_type, p_actor_id, 'anonymize_end_customer', 'end_customers', p_id,
    jsonb_build_object('fields_anonymized',
      jsonb_build_array('phone', 'name', 'extra_data', 'channel_user_id', 'notifications_log.last_error')));
end
$$;

revoke all on function public.anonymize_end_customer_core(uuid, text, uuid)
  from public, anon, authenticated, service_role;

-- Función pública. El actor se deriva del rol real que llamó (session_user), nunca de un parámetro:
-- un parámetro lo podría falsificar cualquier caller para hacerse pasar por platform_admin y cruzar tenants.
create function public.anonymize_end_customer(p_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor_type text := public.audit_actor_type(session_user);
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
begin
  if v_actor_type is null then
    raise exception 'rol no autorizado para anonimizar: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  perform public.anonymize_end_customer_core(p_id, v_actor_type, v_actor_id);
end
$$;

revoke all on function public.anonymize_end_customer(uuid) from public, anon, authenticated, service_role;
grant execute on function public.anonymize_end_customer(uuid) to bot_service, dashboard_service, platform_admin;

-- ---------------------------------------------------------------------------
-- Purga de tenant (§2.2, §3.2, §5.4.1)
-- ---------------------------------------------------------------------------

-- Tenants inactive con el período de gracia vencido, y hace cuántos días (devuelve solo ids y días).
-- El período sale de platform_settings ('tenant_deletion_grace_period_days'); si la fila falta o no es un
-- entero no negativo, no vence ninguno: nunca se interpreta "sin configurar" como 0 días.
create function public.list_tenants_due_for_purge()
returns table (tenant_id uuid, days_overdue integer)
language sql
stable
security definer
set search_path = ''
as $$
  with cfg as (
    select case
             when jsonb_typeof(ps.value) = 'number' and (ps.value #>> '{}') ~ '^[0-9]{1,5}$'
               then (ps.value #>> '{}')::integer
           end as grace_days
      from public.platform_settings ps
     where ps.key = 'tenant_deletion_grace_period_days'
  )
  select t.id,
         floor(extract(epoch from (now() - (t.deactivated_at + make_interval(days => cfg.grace_days)))) / 86400)::integer
    from public.tenants t
   cross join cfg
   where t.status = 'inactive'
     and t.deactivated_at is not null
     and cfg.grace_days is not null
     and t.deactivated_at + make_interval(days => cfg.grace_days) <= now()
   order by t.deactivated_at, t.id
$$;

revoke all on function public.list_tenants_due_for_purge() from public, anon, authenticated, service_role;
grant execute on function public.list_tenants_due_for_purge() to purge_job;

-- Pasos 1 de la cascada de §2.2. La base re-valida inactive + período vencido: un bug del job no puede
-- purgar un tenant activo. Devuelve los auth_user_id que el job debe eliminar vía Auth Admin API.
-- Cada paso deja audit_log (action = 'tenant_purge_step', solo conteos) y es idempotente: reintentar sobre
-- lo ya anonimizado/eliminado no falla; la corrida siguiente retoma una cascada interrumpida.
create function public.purge_tenant_data(p_tenant_id uuid)
returns setof uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_id uuid;
  v_n integer;
  v_secret_ids uuid[];
begin
  -- el lock evita que una reactivación concurrente cruce la validación
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status <> 'inactive' then
    raise exception 'purga rechazada: el tenant no está inactive' using errcode = 'check_violation';
  end if;
  if not exists (select 1 from public.list_tenants_due_for_purge() l where l.tenant_id = p_tenant_id) then
    raise exception 'purga rechazada: el período de gracia no venció' using errcode = 'check_violation';
  end if;

  -- 1) end_customers (anonymize_end_customer_core con actor system, como session_user = purge_job)
  v_n := 0;
  for v_id in
    select ec.id from public.end_customers ec
     where ec.tenant_id = p_tenant_id and ec.deleted_at is null
     order by ec.id
  loop
    perform public.anonymize_end_customer_core(v_id, 'system', v_actor_id);
    v_n := v_n + 1;
  end loop;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'anonymize_end_customers', 'count', v_n));

  -- 2) sesiones del bot que quedaron (de personas sin end_customer: su identificador es el teléfono)
  delete from public.bot_sessions bs where bs.tenant_id = p_tenant_id;
  get diagnostics v_n = row_count;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'delete_bot_sessions', 'count', v_n));

  -- 3) secrets de Vault de todas sus integraciones: las credenciales quedan inválidas
  select coalesce(array_agg(ti.secret_id), '{}'::uuid[]) into v_secret_ids
    from public.tenant_integrations ti
   where ti.tenant_id = p_tenant_id and ti.secret_id is not null;
  delete from vault.secrets s where s.id = any (v_secret_ids);
  get diagnostics v_n = row_count;
  update public.tenant_integrations ti set secret_id = null
   where ti.tenant_id = p_tenant_id and ti.secret_id is not null;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'delete_vault_secrets', 'count', v_n));

  -- 4) cuentas de Auth a eliminar por el job (la fila de tenant_users sobrevive con auth_user_id null)
  select count(*) into v_n
    from public.tenant_users tu
   where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'collect_auth_users', 'count', v_n));

  return query
    select tu.auth_user_id
      from public.tenant_users tu
     where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null
     order by tu.id;
end
$$;

revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role;
grant execute on function public.purge_tenant_data(uuid) to purge_job;

-- Paso 3 de §2.2: recién con todas las cuentas eliminadas setea purged y libera el slug renombrándolo a
-- <slug>-purged-<fecha>; si el negocio vuelve a contratar, se da de alta como una fila nueva. Devuelve el slug
-- resultante. Idempotente: sobre un tenant ya purged devuelve su slug sin tocar nada.
create function public.mark_tenant_purged(p_tenant_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
  v_slug text;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
begin
  select t.status, t.slug into v_status, v_slug from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status = 'purged' then
    return v_slug;
  end if;
  if v_status <> 'inactive' then
    raise exception 'purga rechazada: el tenant no está inactive' using errcode = 'check_violation';
  end if;
  if not exists (select 1 from public.list_tenants_due_for_purge() l where l.tenant_id = p_tenant_id) then
    raise exception 'purga rechazada: el período de gracia no venció' using errcode = 'check_violation';
  end if;
  if exists (select 1 from public.tenant_users tu where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null) then
    raise exception 'no se puede marcar purged: quedan cuentas vinculadas' using errcode = 'check_violation';
  end if;

  v_slug := v_slug || '-purged-' || to_char(timezone('America/Argentina/Buenos_Aires', now()), 'YYYYMMDD');
  update public.tenants t set status = 'purged', slug = v_slug where t.id = p_tenant_id;

  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'mark_tenant_purged'));

  return v_slug;
end
$$;

revoke all on function public.mark_tenant_purged(uuid) from public, anon, authenticated, service_role;
grant execute on function public.mark_tenant_purged(uuid) to purge_job;

-- Limpieza diaria de inbound_messages (§2.10, §5.4.1): los canales reentregan durante horas, no semanas.
-- Devuelve cuántas filas borró.
create function public.prune_inbound_messages()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  delete from public.inbound_messages im where im.received_at < now() - interval '7 days';
  get diagnostics v_n = row_count;
  return v_n;
end
$$;

revoke all on function public.prune_inbound_messages() from public, anon, authenticated, service_role;
grant execute on function public.prune_inbound_messages() to purge_job;
