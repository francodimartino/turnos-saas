-- Servicio enviador: leer lo ya registrado en `entregas` y no perder lo entregado al agotar los reclamos (§6.4).
-- Migración aditiva: una función nueva y el cuerpo de claim_notification_outbox (misma firma, mismos grants).
--
-- 1) consultar_entregas_outbox(p_id)
--    Devuelve una fila (clave, estado, error) por cada canal registrado en `entregas` de una fila del outbox del
--    tenant del contexto. El enviador la usa para no reintentar un canal que ya falló de forma permanente
--    (resolve_outbox_delivery solo marca `entregado` los `sent`). `error` es el código corto que guardó
--    registrar_entrega_outbox (nunca el identificador del canal). Exige contexto de tenant: una fila de otro negocio
--    no devuelve nada. EXECUTE solo para enviador.
--
-- 2) claim_notification_outbox
--    Una fila `pending` que agotó los reclamos (attempts >= p_max_attempts) y tiene al menos un canal `sent` en
--    `entregas` se cierra `sent`, no `failed`: la persona recibió el aviso por ese canal, y el proceso murió o se
--    cortó por plazo antes de poder cerrar la fila. Sin ningún canal `sent` el comportamiento no cambia (`failed`
--    con "intentos agotados sin confirmacion de envio"), así que para las filas sin entregas (las del job de
--    vencimiento) esta función hace lo mismo que antes.

create function public.consultar_entregas_outbox(p_id uuid)
returns table (clave text, estado text, error text)
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
  select e.key, e.value ->> 'estado', e.value ->> 'error'
    from public.notification_outbox o
   cross join lateral jsonb_each(o.entregas) as e
   where o.id = p_id and o.tenant_id = v_ctx;
end
$$;

revoke all on function public.consultar_entregas_outbox(uuid) from public, anon, authenticated, service_role;
grant execute on function public.consultar_entregas_outbox(uuid) to enviador;

create or replace function public.claim_notification_outbox(
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

  -- vencidas que agotaron los reclamos pero ya entregaron por algún canal: cumplidas, no fallidas
  update public.notification_outbox o
     set status = 'sent',
         last_error = 'intentos agotados con entrega parcial',
         processed_at = now()
   where o.status = 'pending'
     and o.next_attempt_at <= now()
     and o.attempts >= p_max_attempts
     and exists (select 1 from jsonb_each(o.entregas) e where e.value ->> 'estado' = 'sent');

  -- vencidas que ya agotaron los reclamos y no entregaron nada: dead-letter, no se reprocesan
  update public.notification_outbox o
     set status = 'failed',
         last_error = 'intentos agotados sin confirmacion de envio',
         processed_at = now()
   where o.status = 'pending'
     and o.next_attempt_at <= now()
     and o.attempts >= p_max_attempts;

  return query
  with elegidas as (
    select q.id
      from public.notification_outbox q
     where q.status = 'pending' and q.next_attempt_at <= now()
     order by q.next_attempt_at, q.id
     limit p_limit
       for update skip locked
  )
  update public.notification_outbox o
     set attempts = o.attempts + 1,
         next_attempt_at = now() + make_interval(secs => p_lease_seconds)
    from elegidas e
   where o.id = e.id
  returning o.id, o.tenant_id, o.end_customer_id, o.channel, o.template, o.params, o.attempts;
end
$$;
