-- El bot identifica y vincula a la persona por canales_cliente (§2.12), sin tocar channel ni channel_user_id de
-- end_customers. Dos piezas de base lo necesitan:
--
-- 0) Copia idempotente de los canales actuales: las fichas que creó el bot anterior después de la migración que
--    creó canales_cliente (y antes de desplegar este código) quedan con su canal. Lo ya copiado no se duplica.
-- 1) La anonimización toma el canal de chat de canales_cliente (con respaldo en las columnas viejas para las fichas
--    que todavía las tengan) y guarda el pseudónimo en end_customers, así el recontacto de §2.3 (find_dormant_end_customer)
--    sigue funcionando para las fichas nuevas. Idéntica a la versión vigente
--    (20260103003500_anonimizar_canales_cliente.sql) salvo esa derivación; misma firma y mismos grants.
-- 2) reactivate_end_customer: reactiva una ficha anonimizada, borra el pseudónimo y crea el canal verificado.
--    Verifica que el pseudónimo de la ficha corresponda al identificador que escribe, para que nadie reactive
--    una ficha ajena conociendo solo su id.

-- Copia idempotente (ver 0): cada (channel, channel_user_id) vigente pasa como canal verificado.
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at, created_at)
select tenant_id, id, channel, channel_user_id, created_at, created_at
  from public.end_customers
 where channel is not null and channel_user_id is not null
on conflict (tenant_id, canal, identificador) do nothing;

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
      jsonb_build_array('phone', 'name', 'extra_data', 'channel_user_id', 'payments.payer',
                        'notifications_log.last_error', 'notification_outbox', 'canales_cliente'),
      'bookings_cancelled', v_cancelados));
end
$$;

-- Reactivación tras el recontacto (§2.3). Solo bot_service; el tenant sale del contexto, nunca de un parámetro.
create function public.reactivate_end_customer(p_id uuid, p_channel text, p_channel_user_id text)
returns setof public.end_customers
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_hash text;
  v_fila public.end_customers;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  v_hash := public.pseudonymize_channel_user(v_ctx, p_channel, p_channel_user_id);
  if v_hash is null then
    return;
  end if;

  -- las columnas viejas quedan en null: el canal vive en canales_cliente
  update public.end_customers ec
     set channel = null,
         channel_user_hash = null,
         channel_user_id = null,
         deleted_at = null
   where ec.id = p_id
     and ec.tenant_id = v_ctx
     and ec.channel = p_channel
     and ec.channel_user_hash = v_hash
     and ec.channel_user_id is null
     and ec.deleted_at is not null
  returning ec.* into v_fila;
  if not found then
    return;
  end if;

  insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at)
  values (v_ctx, p_id, p_channel, p_channel_user_id, now());

  perform public.write_audit_log('reactivate_end_customer', 'end_customers', p_id,
    jsonb_build_object('canal', p_channel), v_ctx);

  return next v_fila;
end
$$;

revoke all on function public.reactivate_end_customer(uuid, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.reactivate_end_customer(uuid, text, text) to bot_service;

-- ---------------------------------------------------------------------------
-- Compatibilidad con lo que todavía lee channel y channel_user_id de end_customers (avisos del outbox, vencimiento
-- de turnos, dashboard): mientras esas columnas existan, un canal de chat verificado se refleja en la ficha si todavía
-- no tiene uno (los declarados sin verificar no: los avisos van solo a canales verificados, §2.12). La fuente de verdad es canales_cliente; esta copia se retira junto con las columnas.
-- ---------------------------------------------------------------------------
create function public.reflejar_canal_en_end_customer()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.canal in ('telegram', 'whatsapp') and new.verificado_at is not null then
    update public.end_customers ec
       set channel = new.canal,
           channel_user_id = new.identificador
     where ec.tenant_id = new.tenant_id
       and ec.id = new.end_customer_id
       and ec.channel is null
       and ec.channel_user_id is null
       and ec.channel_user_hash is null
       and not exists (select 1 from public.end_customers otro
                        where otro.tenant_id = new.tenant_id
                          and otro.channel = new.canal
                          and otro.channel_user_id = new.identificador);
  end if;
  return null;
end
$$;

revoke all on function public.reflejar_canal_en_end_customer()
  from public, anon, authenticated, service_role;

create trigger canales_cliente_reflejar_en_end_customer
  after insert or update of verificado_at on public.canales_cliente
  for each row execute function public.reflejar_canal_en_end_customer();
