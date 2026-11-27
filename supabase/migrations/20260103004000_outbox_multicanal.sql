-- Outbox multicanal (§2.12, §6.4): un aviso del outbox puede ser para el CLIENTE (por todos sus canales
-- verificados) o para el NEGOCIO (por los canales que el negocio configuró), y el resultado del envío se registra
-- POR CANAL: un canal que falla no impide los demás ni repite el que ya salió.
--
-- Migración aditiva (no borra columnas ni datos). Depende solo de migraciones anteriores a esta numeración:
-- canales_cliente (…003400) y avisos_negocio_config (…003700). Las posteriores que tocan el outbox (…004400, unir
-- fichas) solo leen/actualizan las columnas que ya existían, que acá se mantienen.
--
-- 1) notification_outbox
--    - destino text: 'cliente' (por defecto) | 'negocio'.
--    - entregas jsonb: resultado por canal, {clave: {estado: 'sent'|'failed', error, at}}. La clave identifica el
--      destinatario sin datos personales: 'cc:<id de canales_cliente>' (cliente), 'tu:<id de tenant_users>:email'
--      (negocio) o 'calendar'. Nunca guarda el identificador del canal ni texto.
--    - end_customer_id y channel pasan a admitir NULL, pero solo en las filas del negocio (un CHECK lo exige):
--      un aviso al negocio no pertenece a ninguna ficha de cliente. En las del cliente, channel es el canal por el
--      que se originó (ahora también 'email') y NO limita por dónde se entrega: se entrega por todos los verificados.
--    - template: CHECK sobre la lista de plantillas, ligada al destino. Cliente: avisos del turno (vencimiento,
--      confirmación, rechazo, cancelación, movimiento, reserva) y del cambio de horario (aprobado, rechazado,
--      vencido). Negocio: aviso por reserva, cancelación, movimiento y pedido de aprobación, y sincronización de
--      Calendar (no tiene destinatarios: la procesa el servicio que sincroniza).
--    - tenant_id: FK a tenants (las filas del negocio no lo derivan de una ficha). En las del cliente lo sigue
--      forzando el trigger desde end_customers.
--
-- 2) Funciones (todas SECURITY DEFINER, search_path vacío, sin EXECUTE para PUBLIC/anon/authenticated/service_role)
--    outbox_destinatarios(p_tenant_id, p_outbox_id)  -- interna, sin EXECUTE para ningún rol de aplicación
--      Destinatarios de una fila. Cliente: cada canal verificado de la ficha vigente (telegram y email; WhatsApp
--      todavía no envía); una ficha anonimizada no tiene ninguno. Negocio: el personal activo según
--      avisos_negocio_config (sin fila: el owner y quienes tienen approve_bookings; con fila: recibe_avisos y
--      canal_email) y que tenga email. Sincronización de Calendar: un único destino 'calendar' sin identificador.
--    resolve_outbox_delivery(p_outbox_id)
--      Exige contexto de tenant: solo ve filas 'pending' de ese tenant. Una fila por destinatario, telegram
--      primero. Si no hay ninguno (cliente anonimizado) devuelve UNA fila con channel y channel_user_id en NULL:
--      se distingue de "la fila no existe" (ninguna fila). `entregado` es true si ese canal ya salió.
--      Las columnas anteriores conservan nombre y orden; las nuevas van al final.
--    registrar_entrega_outbox(p_id, p_clave, p_estado, p_error)
--      Registra el resultado de un canal. Solo sobre filas 'pending' del tenant del contexto y claves que
--      correspondan a un destinatario actual. Un canal 'sent' no se pisa (idempotente). Devuelve true si registró.
--    enqueue_notification_negocio(p_template, p_params, p_first_attempt_delay_seconds)
--      Encola un aviso al negocio del tenant del contexto (primer intento inmediato por defecto: no hay envío
--      post-COMMIT del que esperar).
--    claim_notification_outbox, complete_notification_outbox y enqueue_notification NO cambian de firma ni de
--    cuerpo: conservan SKIP LOCKED, arriendo, tope de intentos y sus grants actuales (el rol del enviador recibe
--    los suyos aparte).
--
-- Datos personales: la fila sigue sin guardar texto, nombre, teléfono ni identificador de canal. Los destinatarios
-- se resuelven al enviar. La anonimización borra las filas de la ficha y la purga las del tenant (incluidas las del
-- negocio, que se borran por tenant_id).

-- ---------------------------------------------------------------------------
-- Tabla
-- ---------------------------------------------------------------------------
alter table public.notification_outbox
  add column destino text not null default 'cliente'
    constraint notification_outbox_destino_check check (destino in ('cliente', 'negocio')),
  add column entregas jsonb not null default '{}'::jsonb
    constraint notification_outbox_entregas_check
      check (jsonb_typeof(entregas) = 'object' and length(entregas::text) <= 16000);

alter table public.notification_outbox drop constraint notification_outbox_channel_check;
alter table public.notification_outbox alter column end_customer_id drop not null;
alter table public.notification_outbox alter column channel drop not null;

alter table public.notification_outbox
  add constraint notification_outbox_destino_canal_check check (
    (destino = 'cliente' and end_customer_id is not null
      and channel is not null and channel in ('telegram', 'whatsapp', 'email'))
    or (destino = 'negocio' and end_customer_id is null and channel is null)
  ),
  add constraint notification_outbox_plantilla_check check (
    (destino = 'cliente' and template in (
      'turno_vencido', 'turno_confirmado', 'turno_rechazado', 'turno_cancelado', 'turno_movido',
      'reserva_confirmada', 'reserva_pendiente',
      'cambio_horario_aprobado', 'cambio_horario_rechazado', 'cambio_horario_vencido'))
    or (destino = 'negocio' and template in (
      'aviso_negocio_reserva', 'aviso_negocio_cancelacion', 'aviso_negocio_movimiento',
      'aviso_negocio_aprobacion', 'sincronizacion_calendar'))
  ),
  add constraint notification_outbox_tenant_fkey
    foreign key (tenant_id) references public.tenants (id) on delete cascade;

-- Filas del cliente: tenant_id sale de la ficha (lo que mande la aplicación se ignora). Filas del negocio: lo manda
-- quien encola (la FK a tenants valida que exista) y no cambia después.
create or replace function public.notification_outbox_forzar_tenant_id()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant_id uuid;
begin
  if new.end_customer_id is null then
    if tg_op = 'UPDATE' then
      new.tenant_id := old.tenant_id;
    end if;
    return new;
  end if;

  select ec.tenant_id into v_tenant_id
  from public.end_customers ec
  where ec.id = new.end_customer_id;

  if v_tenant_id is null then
    raise exception 'end_customer % inexistente', new.end_customer_id
      using errcode = 'foreign_key_violation';
  end if;

  new.tenant_id := v_tenant_id;
  return new;
end
$$;

-- ---------------------------------------------------------------------------
-- outbox_destinatarios (interna)
-- ---------------------------------------------------------------------------
create function public.outbox_destinatarios(p_tenant_id uuid, p_outbox_id uuid)
returns table (clave text, canal text, identificador text)
language sql
stable
security definer
set search_path = ''
as $$
  with o as (
    select x.tenant_id, x.destino, x.end_customer_id, x.template
      from public.notification_outbox x
     where x.id = p_outbox_id and x.tenant_id = p_tenant_id
  )
  -- cliente: cada canal verificado de una ficha vigente
  select 'cc:' || cc.id::text, cc.canal, cc.identificador
    from o
    join public.end_customers ec
      on ec.tenant_id = o.tenant_id and ec.id = o.end_customer_id and ec.deleted_at is null
    join public.canales_cliente cc
      on cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id and cc.verificado_at is not null
   where o.destino = 'cliente' and cc.canal in ('telegram', 'email')
  union all
  -- negocio: el personal activo que recibe avisos por email (misma regla que avisos_negocio_destinatarios_email)
  select 'tu:' || tu.id::text || ':email', 'email', coalesce(au.email::text, tu.pending_invite_email)
    from o
    join public.tenant_users tu on tu.tenant_id = o.tenant_id and tu.is_active
    left join auth.users au on au.id = tu.auth_user_id
    left join public.avisos_negocio_config c on c.tenant_id = tu.tenant_id and c.tenant_user_id = tu.id
   where o.destino = 'negocio' and o.template <> 'sincronizacion_calendar'
     and coalesce(au.email::text, tu.pending_invite_email) is not null
     and case
           when c.tenant_user_id is null then
             tu.is_owner or exists (
               select 1 from public.tenant_user_permissions p
                where p.tenant_user_id = tu.id and p.permission_key = 'approve_bookings')
           else c.recibe_avisos and c.canal_email
         end
  union all
  -- sincronización de Calendar: un único destino, sin identificador
  select 'calendar', 'calendar', null::text
    from o
   where o.destino = 'negocio' and o.template = 'sincronizacion_calendar'
$$;

revoke all on function public.outbox_destinatarios(uuid, uuid) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- resolve_outbox_delivery: una fila por destinatario
-- ---------------------------------------------------------------------------
drop function public.resolve_outbox_delivery(uuid);

create function public.resolve_outbox_delivery(p_outbox_id uuid)
returns table (channel text, channel_user_id text, tenant_active boolean, booking_status text,
               service_name text, professional_name text, starts_at timestamptz, pending_expires_at timestamptz,
               destino text, clave text, entregado boolean)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  return query
  select d.canal,
         d.identificador,
         tr.activo,
         tr.estado_turno,
         tr.nombre_servicio,
         tr.nombre_profesional,
         tr.inicio,
         tr.vence,
         tr.destino,
         d.clave,
         coalesce((tr.entregas -> d.clave) ->> 'estado' = 'sent', false)
    from (
      select o.id as outbox_id,
             o.tenant_id as tenant_id,
             o.destino as destino,
             o.entregas as entregas,
             (t.status = 'active') as activo,
             b.status::text as estado_turno,
             s.name as nombre_servicio,
             p.name as nombre_profesional,
             lower(b.time_range) as inicio,
             b.pending_expires_at as vence
        from public.notification_outbox o
        join public.tenants t on t.id = o.tenant_id
        join public.bookings b
          on b.tenant_id = o.tenant_id
         and b.id = case when o.params ->> 'booking_id' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                         then (o.params ->> 'booking_id')::uuid end
        join public.services s on s.tenant_id = b.tenant_id and s.id = b.service_id
        left join public.professionals p on p.tenant_id = b.tenant_id and p.id = b.professional_id
       where o.id = p_outbox_id
         and o.tenant_id = v_ctx
         and o.status = 'pending'
    ) tr
    left join lateral public.outbox_destinatarios(tr.tenant_id, tr.outbox_id) d on true
   order by (d.canal = 'telegram') desc nulls last, d.canal, d.clave;
end
$$;

revoke all on function public.resolve_outbox_delivery(uuid) from public, anon, authenticated, service_role;
-- EXECUTE de scheduler_job (el job de vencimiento sigue resolviendo sus avisos); el rol del enviador lo recibe aparte.
grant execute on function public.resolve_outbox_delivery(uuid) to scheduler_job;

-- ---------------------------------------------------------------------------
-- registrar_entrega_outbox: resultado de un canal
-- ---------------------------------------------------------------------------
create function public.registrar_entrega_outbox(
  p_id uuid,
  p_clave text,
  p_estado text,
  p_error text default null
)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_filas integer;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;
  if p_estado is null or p_estado not in ('sent', 'failed') then
    raise exception 'estado invalido' using errcode = 'invalid_parameter_value';
  end if;
  if p_clave is null or length(p_clave) not between 1 and 100 then
    raise exception 'clave invalida' using errcode = 'invalid_parameter_value';
  end if;

  update public.notification_outbox o
     set entregas = o.entregas || jsonb_build_object(
           p_clave,
           jsonb_build_object('estado', p_estado, 'error', left(p_error, 200), 'at', now()))
   where o.id = p_id
     and o.tenant_id = v_ctx
     and o.status = 'pending'
     and exists (select 1 from public.outbox_destinatarios(o.tenant_id, o.id) d where d.clave = p_clave)
     and ((o.entregas -> p_clave) ->> 'estado') is distinct from 'sent';
  get diagnostics v_filas = row_count;
  return v_filas = 1;
end
$$;

revoke all on function public.registrar_entrega_outbox(uuid, text, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.registrar_entrega_outbox(uuid, text, text, text)
  to bot_service, dashboard_service, scheduler_job;

-- ---------------------------------------------------------------------------
-- enqueue_notification_negocio
-- ---------------------------------------------------------------------------
create function public.enqueue_notification_negocio(
  p_template text,
  p_params jsonb default '{}'::jsonb,
  p_first_attempt_delay_seconds integer default 0
)
returns uuid
language plpgsql
volatile
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
  if p_first_attempt_delay_seconds is null or p_first_attempt_delay_seconds not between 0 and 86400 then
    raise exception 'demora del primer intento invalida' using errcode = 'invalid_parameter_value';
  end if;

  insert into public.notification_outbox (tenant_id, destino, template, params, next_attempt_at)
  values (v_ctx, 'negocio', p_template, coalesce(p_params, '{}'::jsonb),
          now() + make_interval(secs => p_first_attempt_delay_seconds))
  returning id into v_id;

  return v_id;
end
$$;

revoke all on function public.enqueue_notification_negocio(text, jsonb, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_notification_negocio(text, jsonb, integer)
  to bot_service, dashboard_service, scheduler_job;
