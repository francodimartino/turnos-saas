-- Anonimización y purga para la web de reservas (§2.12, §3.5, §2.2).
--
-- 1) audit_log admite el actor 'web' y audit_actor_type mapea el rol web_reservas a él: anonymize_end_customer
--    deriva el actor de session_user, así que hasta ahora rechazaba a web_reservas aunque tuviera EXECUTE.
--    No agrega privilegios de auditoría a ningún rol: web_reservas sigue sin write_audit_log.
-- 2) codigos_web.end_customer_id (nullable): liga un código a la ficha cuando la web la conoce al pedirlo. El
--    código solo guarda el hash del email, que se calcula con una clave que la base no ve, así que sin esta columna no
--    hay forma de borrar los códigos de una persona al anonimizarla. web_reservas puede escribirla al insertar.
-- 3) anonymize_end_customer_core (versión vigente: 20260103003910_bot_usa_canales_cliente.sql) acepta el actor 'web'
--    (con contexto de tenant exigido, igual que bot y staff), pone en null email, politica_version y
--    politica_aceptada_at y borra las sesiones y los códigos de la ficha. Misma firma y mismos grants.
-- 4) purge_tenant_data (versión vigente: 20260103003500_anonimizar_canales_cliente.sql) borra además sesiones_web,
--    codigos_web, avisos_negocio_config y slugs_anteriores del tenant y devuelve, junto a cada cuenta de Auth, la ruta
--    del logo (settings.logo_ruta) para que el job borre el archivo de Storage. Cambia el tipo de retorno, así que se
--    recrea con drop function: mismos grants (EXECUTE solo para purge_job).

-- ---------------------------------------------------------------------------
-- 1) Actor 'web'
-- ---------------------------------------------------------------------------
do $$
declare
  v_nombre text;
begin
  select c.conname into v_nombre
    from pg_constraint c
   where c.conrelid = 'public.audit_log'::regclass and c.contype = 'c'
     and pg_get_constraintdef(c.oid) like '%actor_type%';
  if v_nombre is not null then
    execute format('alter table public.audit_log drop constraint %I', v_nombre);
  end if;
end
$$;

alter table public.audit_log
  add constraint audit_log_actor_type_check
  check (actor_type in ('staff', 'bot', 'web', 'system', 'platform_admin'));

create or replace function public.audit_actor_type(p_session_user text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case p_session_user
    when 'bot_service' then 'bot'
    when 'dashboard_service' then 'staff'
    when 'web_reservas' then 'web'
    when 'platform_admin' then 'platform_admin'
    when 'purge_job' then 'system'
  end
$$;

-- ---------------------------------------------------------------------------
-- 2) Códigos ligados a la ficha
-- ---------------------------------------------------------------------------
alter table public.codigos_web add column if not exists end_customer_id uuid null;

alter table public.codigos_web
  add constraint codigos_web_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id)
    on delete cascade;

create index codigos_web_end_customer_idx on public.codigos_web (tenant_id, end_customer_id)
  where end_customer_id is not null;

grant insert (end_customer_id) on public.codigos_web to web_reservas;

-- ---------------------------------------------------------------------------
-- 3) Anonimización
-- ---------------------------------------------------------------------------
create or replace function public.anonymize_end_customer_core(p_id uuid, p_actor_type text, p_actor_id uuid)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_tenant_id uuid;
  v_channel text;
  v_channel_user_id text;
  v_cancelados integer;
  v_canal_chat text;
  v_identificador_chat text;
  v_pedido_canal text := nullif(current_setting('app.requesting_channel', true), '');
  v_pedido_id text := nullif(current_setting('app.requesting_channel_user_id', true), '');
begin
  if p_actor_type is null or p_actor_type not in ('bot', 'staff', 'web', 'platform_admin', 'system') then
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

  -- El canal de chat (telegram o whatsapp) de la persona vive en canales_cliente; las columnas channel y
  -- channel_user_id de end_customers quedan solo como respaldo para fichas que todavía las tengan. La ficha guarda un
  -- único pseudónimo: el del canal desde el que se pidió el borrado (el bot lo informa en app.requesting_channel y
  -- app.requesting_channel_user_id, y solo vale si ese canal es de esta ficha), para que el recontacto funcione desde
  -- esa cuenta; sin ese dato, el de Telegram y, si no, el más antiguo.
  select cc.canal, cc.identificador
    into v_canal_chat, v_identificador_chat
    from public.canales_cliente cc
   where cc.tenant_id = v_tenant_id
     and cc.end_customer_id = p_id
     and cc.canal in ('telegram', 'whatsapp')
   order by (cc.canal = v_pedido_canal and cc.identificador = v_pedido_id) desc,
            (cc.canal = 'telegram') desc, cc.created_at, cc.id
   limit 1;
  if found then
    v_channel := v_canal_chat;
    v_channel_user_id := v_identificador_chat;
  end if;

  -- Idempotente: si channel_user_id ya es null (ya anonimizado) se conserva el hash existente.
  -- Sin la clave HMAC en Vault pseudonymize_channel_user devuelve NULL y el check de end_customers
  -- (canal <=> identificador o hash) hace fallar la operación: falla cerrada, sin dejar la fila a medias.
  update public.end_customers ec
     set phone = null,
         name = null,
         email = null,
         politica_version = null,
         politica_aceptada_at = null,
         extra_data = '{}'::jsonb,
         deleted_at = coalesce(ec.deleted_at, now()),
         channel = v_channel,
         channel_user_hash = case
           when v_channel_user_id is null then ec.channel_user_hash
           else public.pseudonymize_channel_user(v_tenant_id, v_channel, v_channel_user_id)
         end,
         channel_user_id = null
   where ec.id = p_id;

  -- copias de PII fuera de end_customers (§2.10)

  -- datos del pagador en el payload crudo del webhook (de los bookings y packs del cliente);
  -- provider_payment_id y el resto del payload se conservan: permiten consultar el pago en Mercado Pago
  update public.payments p
     set webhook_payload = (((p.webhook_payload - 'payer') #- '{additional_info,payer}') #- '{card,cardholder}')
   where p.tenant_id = v_tenant_id
     and p.webhook_payload is not null
     and ((p.payable_type = 'booking_deposit'
           and p.payable_id in (select b.id from public.bookings b
                                 where b.tenant_id = v_tenant_id and b.end_customer_id = p_id))
          or (p.payable_type = 'booking_pack'
              and p.payable_id in (select bp.id from public.booking_packs bp
                                    where bp.tenant_id = v_tenant_id and bp.end_customer_id = p_id)));

  update public.notifications_log nl
     set last_error = null
   where nl.end_customer_id = p_id and nl.tenant_id = v_tenant_id;

  -- avisos pendientes de enviar (notification_outbox): se borran, no se anonimizan; la anonimización los descarta
  delete from public.notification_outbox o
   where o.end_customer_id = p_id and o.tenant_id = v_tenant_id;

  -- sesiones de todos los canales de chat de la ficha (con el identificador en claro), antes de borrar los canales
  delete from public.bot_sessions bs
   using public.canales_cliente cc
   where cc.tenant_id = v_tenant_id
     and cc.end_customer_id = p_id
     and bs.tenant_id = v_tenant_id
     and bs.channel = cc.canal
     and bs.channel_user_id = cc.identificador;
  delete from public.bot_sessions bs
   where bs.tenant_id = v_tenant_id
     and bs.channel = v_channel
     and bs.channel_user_id = v_channel_user_id;

  -- canales de la ficha (canales_cliente): guardan el identificador en claro (p. ej. el id de Telegram), así que se
  -- borran; la unicidad (tenant, canal, identificador) queda libre
  delete from public.canales_cliente cc
   where cc.end_customer_id = p_id and cc.tenant_id = v_tenant_id;

  -- acceso web: las sesiones de la ficha y los códigos que se pidieron para ella
  delete from public.sesiones_web sw
   where sw.tenant_id = v_tenant_id and sw.end_customer_id = p_id;
  delete from public.codigos_web cw
   where cw.tenant_id = v_tenant_id and cw.end_customer_id = p_id;

  -- turnos futuros pendientes: se cancelan y liberan el horario (decisión de §3.5). La sesión del pack, si
  -- usaban una, la devuelve el trigger AFTER UPDATE de bookings, nunca esta función. Los confirmed quedan:
  -- el dashboard los muestra "sin cliente identificable" (end_customers.deleted_at).
  update public.bookings b
     set status = 'cancelled',
         pending_expires_at = null
   where b.tenant_id = v_tenant_id
     and b.end_customer_id = p_id
     and b.status = 'pending'
     and lower(b.time_range) > now();
  get diagnostics v_cancelados = row_count;


  -- constancia del borrado: fecha, actor y campos, sin retener ningún valor original
  perform public.audit_log_append(
    v_tenant_id, p_actor_type, p_actor_id, 'anonymize_end_customer', 'end_customers', p_id,
    jsonb_build_object('fields_anonymized',
      jsonb_build_array('phone', 'name', 'email', 'extra_data', 'channel_user_id', 'politica_version',
                        'politica_aceptada_at', 'payments.payer', 'notifications_log.last_error',
                        'notification_outbox', 'canales_cliente', 'sesiones_web', 'codigos_web'),
      'bookings_cancelled', v_cancelados));
end
$$;

-- ---------------------------------------------------------------------------
-- 4) Purga de tenant
-- ---------------------------------------------------------------------------
drop function public.purge_tenant_data(uuid);

create function public.purge_tenant_data(p_tenant_id uuid)
returns table (auth_user_id uuid, logo_ruta text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  c_nombre_anonimo constant text := 'Profesional anonimizado';
  v_status text;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_id uuid;
  v_n integer;
  v_profesionales integer;
  v_secret_ids uuid[];
  v_emails integer;
  v_outbox integer;
  v_sesiones integer;
  v_codigos integer;
  v_avisos_config integer;
  v_slugs integer;
  v_logo text;
begin
  -- el lock evita que una reactivación concurrente cruce la validación
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status = 'inactive' then
    -- valida inactive + gracia vencida bajo lock y pasa a purging (o rechaza): desde acá no se puede reactivar
    perform public.begin_tenant_purge(p_tenant_id);
  elsif v_status <> 'purging' then
    raise exception 'purga rechazada: el tenant no está inactive ni en purga' using errcode = 'check_violation';
  end if;

  -- 1) end_customers (anonymize_end_customer_core con actor system, como session_user = purge_job)
  --    y el nombre de los professionals del tenant
  v_n := 0;
  for v_id in
    select ec.id from public.end_customers ec
     where ec.tenant_id = p_tenant_id and ec.deleted_at is null
     order by ec.id
  loop
    perform public.anonymize_end_customer_core(v_id, 'system', v_actor_id);
    v_n := v_n + 1;
  end loop;

  -- avisos del outbox que pudieran quedar (p. ej. de un end_customer ya anonimizado antes): se borran todos
  delete from public.notification_outbox o where o.tenant_id = p_tenant_id;
  get diagnostics v_outbox = row_count;

  -- canales que pudieran quedar (de una ficha ya anonimizada antes): se borran todos
  delete from public.canales_cliente cc where cc.tenant_id = p_tenant_id;

  -- sesiones y códigos de la web (los códigos solo guardan hashes y no siempre se ligan a una ficha), la
  -- configuración de avisos al negocio y los slugs anteriores: no queda ninguna fila del tenant
  delete from public.sesiones_web sw where sw.tenant_id = p_tenant_id;
  get diagnostics v_sesiones = row_count;
  delete from public.codigos_web cw where cw.tenant_id = p_tenant_id;
  get diagnostics v_codigos = row_count;
  delete from public.avisos_negocio_config anc where anc.tenant_id = p_tenant_id;
  get diagnostics v_avisos_config = row_count;
  delete from public.slugs_anteriores sa where sa.tenant_id = p_tenant_id;
  get diagnostics v_slugs = row_count;

  -- ruta del logo en Storage: se devuelve para que el job borre el archivo. La purga no la quita de settings, así
  -- que si el job se interrumpe antes de borrar el archivo, la corrida siguiente la vuelve a recibir
  select t.settings ->> 'logo_ruta' into v_logo from public.tenants t where t.id = p_tenant_id;

  update public.professionals p
     set name = c_nombre_anonimo
   where p.tenant_id = p_tenant_id and p.name <> c_nombre_anonimo;
  get diagnostics v_profesionales = row_count;

  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'anonymize_end_customers', 'count', v_n,
                       'professionals_anonymized', v_profesionales,
                       'outbox_deleted', v_outbox,
                       'sesiones_web_deleted', v_sesiones, 'codigos_web_deleted', v_codigos,
                       'avisos_negocio_config_deleted', v_avisos_config, 'slugs_anteriores_deleted', v_slugs,
                       'logo_pendiente_de_borrar', v_logo is not null));

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
  -- y el email y nombre de invitación pendiente (PII) de los que nunca vincularon cuenta
  update public.tenant_users tu set pending_invite_email = null, pending_invite_name = null
   where tu.tenant_id = p_tenant_id
     and (tu.pending_invite_email is not null or tu.pending_invite_name is not null);
  get diagnostics v_emails = row_count;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'collect_auth_users', 'count', v_n, 'pending_invite_emails_cleared', v_emails));

  -- una fila por cuenta de Auth, todas con la ruta del logo; si no hay cuentas pero sí logo, una única fila con la
  -- cuenta en null (quien consume el resultado ignora las cuentas nulas)
  return query
    select tu.auth_user_id, v_logo
      from public.tenant_users tu
     where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null
     order by tu.id;
  if not found and v_logo is not null then
    return query select null::uuid, v_logo;
  end if;
end
$$;

revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role;
grant execute on function public.purge_tenant_data(uuid) to purge_job;
