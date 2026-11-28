-- Outbox multicanal (§2.12, §6.4): un aviso del outbox puede ser para el CLIENTE o para el NEGOCIO, y el resultado
-- del envío se puede registrar POR CANAL (columna entregas y registrar_entrega_outbox).
--
-- Qué hace esta migración y qué pasa hoy (cuatro cosas distintas):
--   Destinatarios: resolve_outbox_delivery devuelve, para una fila del cliente, sus canales verificados telegram y
--   email (el `channel` de la fila es el canal de origen y no limita los destinatarios que devuelve la función); para
--   una fila del negocio, el personal que recibe avisos según avisos_negocio_config.
--   Envío inmediato (posterior al COMMIT): el bot y el dashboard envían solo por Telegram y solo si la ficha tiene
--   como único canal verificado un Telegram; envían y cierran la fila (sent o failed). Con otro canal verificado no
--   envían ni cierran: la fila queda pending. El del job de vencimiento al vencer un turno envía solo por Telegram y
--   cierra la fila aunque la ficha tenga otros canales verificados; el email no se envía.
--   Reescaneo del job de vencimiento: solo retoma filas que el envío inmediato no cerró, y solo de fichas cuyo único
--   canal verificado es un Telegram.
--   Filas pendientes: las que el envío inmediato no cerró y son del negocio, de Calendar o de clientes con otro
--   canal verificado o con más de un Telegram quedan pending: hoy ningún proceso las envía. Hoy ningún proceso
--   envía por todos los canales.
--
-- Nombres: las funciones nuevas conservan los parámetros en inglés (p_limit, p_lease_seconds, p_outbox_id...) por
-- paralelismo con sus hermanas (claim_notification_outbox, enqueue_notification, complete_notification_outbox); es
-- una excepción deliberada a los nombres en español.
--
-- Migración aditiva (no borra columnas ni datos). Depende solo de migraciones anteriores a esta numeración:
-- canales_cliente (…003400) y avisos_negocio_config (…003700). Las posteriores que tocan el outbox (…004400, unir
-- fichas) solo leen/actualizan las columnas que ya existían, que acá se mantienen.
--
-- 1) notification_outbox
--    - destino text: 'cliente' (por defecto) | 'negocio'.
--    - entregas jsonb: resultado por canal, {clave: {estado: 'sent'|'failed', error, at}}: clave, estado, fecha y
--      un texto de error de hasta 200 caracteres (el código o mensaje del proveedor, sin el texto enviado). La clave
--      identifica el destinatario sin datos personales: 'cc:<id de canales_cliente>' (cliente),
--      'tu:<id de tenant_users>:email' (negocio) o 'calendar'. Nunca guarda el identificador del canal.
--    - end_customer_id y channel pasan a admitir NULL, pero solo en las filas del negocio (un CHECK lo exige):
--      un aviso al negocio no pertenece a ninguna ficha de cliente. En las del cliente, channel es el canal por el
--      que se originó (ahora también 'email'); no limita los destinatarios que devuelve resolve_outbox_delivery.
--    - template: CHECK sobre la lista de plantillas, ligada al destino. Cliente: avisos del turno (vencimiento,
--      confirmación, rechazo, cancelación, movimiento, reserva) y del cambio de horario (aprobado, rechazado,
--      vencido). Negocio: aviso por reserva, cancelación, movimiento y pedido de aprobación, y sincronización de
--      Calendar (no tiene destinatarios de aviso: su único destino es 'calendar').
--    - tenant_id: FK a tenants (las filas del negocio no lo derivan de una ficha). En las del cliente lo sigue
--      forzando el trigger desde end_customers.
--
-- 2) Funciones (todas SECURITY DEFINER, search_path vacío, sin EXECUTE para PUBLIC/anon/authenticated/service_role)
--    outbox_destinatarios(p_tenant_id, p_outbox_id)  -- interna, sin EXECUTE para ningún rol de aplicación
--      Destinatarios de una fila. Cliente: cada canal verificado de la ficha vigente (telegram y email; WhatsApp
--      no se resuelve todavía); una ficha anonimizada no tiene ninguno. Negocio: el personal activo según
--      avisos_negocio_config (sin fila: el owner y quienes tienen approve_bookings; con fila: recibe_avisos y
--      canal_email) y que tenga email. Sincronización de Calendar: un único destino 'calendar' sin identificador.
--    resolve_outbox_delivery(p_outbox_id)
--      Exige contexto de tenant: solo ve filas 'pending' de ese tenant. Una fila por destinatario, telegram
--      primero. Si no hay ninguno (ficha anonimizada o sin canales verificados utilizables) devuelve UNA fila con
--      channel y channel_user_id en NULL: se distingue de "la fila no existe" (ninguna fila). `entregado` es true si
--      el resultado de ese canal ya figura registrado como sent en `entregas`.
--      `anonimizada` es true solo si la ficha del cliente está anonimizada: distingue "ficha anonimizada" de "ficha
--      vigente sin canales verificados que se puedan usar" (ambas devuelven la fila sin canal).
--      Las columnas anteriores conservan nombre y orden; las nuevas van al final.
--    registrar_entrega_outbox(p_id, p_clave, p_estado, p_error)
--      Registra el resultado de un canal. Solo sobre filas 'pending' del tenant del contexto y claves que
--      correspondan a un destinatario actual. Un canal 'sent' no se pisa (idempotente). Devuelve true si registró.
--      EXECUTE solo para scheduler_job: marcar un canal como entregado lo omite en los reintentos siguientes, así que
--      no se concede a roles que no envían. Hoy ningún proceso del repositorio la llama.
--    encolar_aviso_negocio(p_template, p_params, p_first_attempt_delay_seconds)
--      Encola un aviso al negocio del tenant del contexto. p_params exige booking_id (uuid): sin él la fila no se
--      podría resolver (22023). Primer intento inmediato por defecto (next_attempt_at = ahora): a diferencia de los
--      avisos al cliente, no hay envío inmediato posterior al COMMIT que tenga prioridad sobre la fila.
--    reclamar_avisos_telegram(p_limit, p_lease_seconds, p_max_attempts)
--      El reclamo del reescaneo del job de vencimiento (que solo envía por Telegram): como
--      claim_notification_outbox, pero solo toma filas del cliente con plantilla de turno cuya ficha vigente tiene
--      como ÚNICO canal verificado un Telegram. Las demás (negocio, Calendar, clientes con email u otro canal
--      verificado, más de un Telegram, plantillas de cambio de horario) no se reclaman, no consumen intentos y el
--      reescaneo no las cierra: si el envío inmediato tampoco las cerró, quedan pending y hoy ningún proceso las
--      envía. Las filas de una ficha de un único Telegram cuyo envío ya figura registrado en `entregas` se cierran
--      `sent` en vez de reclamarse.
--    claim_notification_outbox, complete_notification_outbox y enqueue_notification NO cambian de firma ni de
--    cuerpo: conservan SKIP LOCKED, arriendo, tope de intentos y sus grants actuales.
--
-- Datos personales: la fila sigue sin guardar el texto del aviso, nombre, teléfono ni identificador de canal (entregas
-- solo lleva claves con ids, estados, fechas y el texto de error del proveedor). Los destinatarios se resuelven
-- al enviar. La anonimización borra las filas de la ficha y la purga las del tenant (incluidas las del
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
-- (Parámetros p_* en inglés por paralelismo con claim_notification_outbox.)
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
-- (Parámetros p_* en inglés por paralelismo con la versión anterior de esta función.)
drop function public.resolve_outbox_delivery(uuid);

create function public.resolve_outbox_delivery(p_outbox_id uuid)
returns table (channel text, channel_user_id text, tenant_active boolean, booking_status text,
               service_name text, professional_name text, starts_at timestamptz, pending_expires_at timestamptz,
               destino text, clave text, entregado boolean, anonimizada boolean)
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
         coalesce((tr.entregas -> d.clave) ->> 'estado' = 'sent', false),
         tr.anonimizada
    from (
      select o.id as outbox_id,
             o.tenant_id as tenant_id,
             o.destino as destino,
             o.entregas as entregas,
             coalesce(ec.deleted_at is not null, false) as anonimizada,
             (t.status = 'active') as activo,
             b.status::text as estado_turno,
             s.name as nombre_servicio,
             p.name as nombre_profesional,
             lower(b.time_range) as inicio,
             b.pending_expires_at as vence
        from public.notification_outbox o
        join public.tenants t on t.id = o.tenant_id
        left join public.end_customers ec on ec.tenant_id = o.tenant_id and ec.id = o.end_customer_id
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
-- EXECUTE de scheduler_job: el job de vencimiento resuelve con él los avisos que reclama.
grant execute on function public.resolve_outbox_delivery(uuid) to scheduler_job;

-- ---------------------------------------------------------------------------
-- registrar_entrega_outbox: resultado de un canal
-- ---------------------------------------------------------------------------
-- (Parámetros p_* en inglés por paralelismo con complete_notification_outbox.)
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
  to scheduler_job;

-- ---------------------------------------------------------------------------
-- encolar_aviso_negocio
-- ---------------------------------------------------------------------------
-- (Parámetros p_* en inglés por paralelismo con enqueue_notification.)
create function public.encolar_aviso_negocio(
  p_template text,
  p_params jsonb,
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
  -- resolve_outbox_delivery arma el aviso desde el turno: sin booking_id la fila nunca devolvería nada
  if p_params is null or (p_params ->> 'booking_id') is null
     or (p_params ->> 'booking_id') !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
    raise exception 'el aviso al negocio exige params.booking_id (uuid)' using errcode = 'invalid_parameter_value';
  end if;

  insert into public.notification_outbox (tenant_id, destino, template, params, next_attempt_at)
  values (v_ctx, 'negocio', p_template, p_params,
          now() + make_interval(secs => p_first_attempt_delay_seconds))
  returning id into v_id;

  return v_id;
end
$$;

revoke all on function public.encolar_aviso_negocio(text, jsonb, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.encolar_aviso_negocio(text, jsonb, integer)
  to bot_service, dashboard_service, scheduler_job;

-- ---------------------------------------------------------------------------
-- reclamar_avisos_telegram: el reclamo del reescaneo del job de vencimiento (solo envía por Telegram)
-- ---------------------------------------------------------------------------
-- (Parámetros p_* en inglés por paralelismo con claim_notification_outbox.)
-- Igual que claim_notification_outbox (SKIP LOCKED, arriendo, tope de intentos y dead-letter), pero solo toma las
-- filas que ese job puede atender: destino cliente, con una plantilla de turno que sabe armar y una ficha vigente
-- cuyo ÚNICO canal verificado es un Telegram (ni email, ni WhatsApp, ni un segundo Telegram verificados). Las demás
-- (negocio, Calendar, clientes con más de un canal verificado, plantillas de cambio de horario) no se reclaman, no
-- consumen intentos y no pasan a failed por este reclamo: si el envío inmediato tampoco las cerró, quedan pending y
-- hoy ningún proceso las envía.
-- Una fila de esas fichas cuyo Telegram ya figura entregado en `entregas` se cierra `sent` (no se reclama ni va a
-- failed).
create function public.reclamar_avisos_telegram(
  p_limit integer default 20,
  p_lease_seconds integer default 300,
  p_max_attempts integer default 5
)
returns table (
  id uuid,
  tenant_id uuid,
  end_customer_id uuid,
  channel text,
  template text,
  params jsonb,
  attempts integer
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
#variable_conflict use_column
begin
  if p_limit is null or p_limit not between 1 and 500
     or p_lease_seconds is null or p_lease_seconds not between 30 and 86400
     or p_max_attempts is null or p_max_attempts not between 2 and 20 then
    raise exception 'parametros invalidos' using errcode = 'invalid_parameter_value';
  end if;

  -- Si el Telegram de la ficha ya figura entregado en `entregas` (quedó registrado y la fila sin cerrar), la fila
  -- está cumplida: se cierra `sent` en vez de reclamarla o mandarla a failed.
  update public.notification_outbox o
     set status = 'sent',
         processed_at = now()
   where o.status = 'pending'
     and o.destino = 'cliente'
     and o.template in ('turno_vencido', 'turno_confirmado', 'turno_rechazado', 'turno_cancelado', 'turno_movido',
                        'reserva_confirmada', 'reserva_pendiente')
     and exists (
         select 1
           from public.end_customers ec
          where ec.tenant_id = o.tenant_id and ec.id = o.end_customer_id and ec.deleted_at is null
            and (select count(*) from public.canales_cliente cc
                  where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                    and cc.verificado_at is not null) = 1
            and exists (select 1 from public.canales_cliente cc
                         where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                           and coalesce((o.entregas -> ('cc:' || cc.id::text)) ->> 'estado', '') = 'sent'
                           and cc.canal = 'telegram' and cc.verificado_at is not null));

  update public.notification_outbox o
     set status = 'failed',
         last_error = 'intentos agotados sin confirmacion de envio',
         processed_at = now()
   where o.status = 'pending'
     and o.next_attempt_at <= now()
     and o.attempts >= p_max_attempts
     and o.destino = 'cliente'
     and o.template in ('turno_vencido', 'turno_confirmado', 'turno_rechazado', 'turno_cancelado', 'turno_movido',
                        'reserva_confirmada', 'reserva_pendiente')
       and exists (
         select 1
           from public.end_customers ec
          where ec.tenant_id = o.tenant_id and ec.id = o.end_customer_id and ec.deleted_at is null
            and (select count(*) from public.canales_cliente cc
                  where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                    and cc.verificado_at is not null) = 1
            and exists (select 1 from public.canales_cliente cc
                         where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                           and cc.canal = 'telegram' and cc.verificado_at is not null));

  return query
  with elegidas as (
    select q.id
      from public.notification_outbox q
     where q.status = 'pending' and q.next_attempt_at <= now()
       and q.destino = 'cliente'
       and q.template in ('turno_vencido', 'turno_confirmado', 'turno_rechazado', 'turno_cancelado', 'turno_movido',
                          'reserva_confirmada', 'reserva_pendiente')
       and exists (
         select 1
           from public.end_customers ec
          where ec.tenant_id = q.tenant_id and ec.id = q.end_customer_id and ec.deleted_at is null
            and (select count(*) from public.canales_cliente cc
                  where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                    and cc.verificado_at is not null) = 1
            and exists (select 1 from public.canales_cliente cc
                         where cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
                           and cc.canal = 'telegram' and cc.verificado_at is not null))
     order by q.next_attempt_at, q.id
     limit p_limit
       for update of q skip locked
  )
  update public.notification_outbox o
     set attempts = o.attempts + 1,
         next_attempt_at = now() + make_interval(secs => p_lease_seconds)
    from elegidas e
   where o.id = e.id
  returning o.id, o.tenant_id, o.end_customer_id, o.channel, o.template, o.params, o.attempts;
end
$$;

revoke all on function public.reclamar_avisos_telegram(integer, integer, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.reclamar_avisos_telegram(integer, integer, integer) to scheduler_job;
