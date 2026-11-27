-- Unir dos fichas de la misma persona (§2.12). unir_end_customers(principal, absorbida) mueve a la principal los
-- turnos, packs, canales, avisos y registros de envío de la absorbida, completa los datos vacíos de la principal
-- con los de la absorbida y anonimiza la absorbida. Todo en la transacción del llamador, con las dos filas
-- bloqueadas, y deja la constancia en audit_log.
--
-- Hace falta una función SECURITY DEFINER porque dashboard_service no tiene ningún permiso sobre
-- notification_outbox (§3.2) y porque la anonimización de la absorbida usa la misma lógica del derecho al olvido.
--
-- El tenant sale siempre del contexto de la transacción (app.current_tenant_id) y el actor, del rol real
-- (session_user) y de app.current_actor_id: ningún parámetro los puede falsificar. Una ficha de otro tenant es
-- indistinguible de una inexistente.
--
-- unir_end_customers_core hace el trabajo y no la ejecuta ningún rol de aplicación; unir_end_customers es la
-- puerta pública.
--
-- Errores (SQLSTATE propios, los traduce el backend):
--   UF001  una de las fichas no existe en el tenant del contexto
--   UF002  una de las fichas ya está anonimizada
--   UF003  las dos fichas tienen el mismo tipo de canal con identificadores distintos
--   UF004  principal y absorbida son la misma ficha

create function public.unir_end_customers_core(
  p_principal uuid, p_absorbida uuid, p_actor_type text, p_actor_id uuid
)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_p public.end_customers%rowtype;
  v_a public.end_customers%rowtype;
  v_turnos integer;
  v_packs integer;
  v_canales integer;
  v_avisos integer;
  v_registros integer;
  v_email_descartado boolean;
  v_telefono_descartado boolean;
begin
  if p_actor_type is null then
    raise exception 'actor no autorizado para unir fichas' using errcode = 'insufficient_privilege';
  end if;
  -- NULL no dispararía un IF sobre una comparación: se exige el contexto de tenant de forma explícita
  if v_tenant is null then
    raise exception 'tenant mismatch: contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;
  if p_principal is null or p_absorbida is null then
    raise exception 'ficha inexistente' using errcode = 'UF001';
  end if;
  if p_principal = p_absorbida then
    raise exception 'no se puede unir una ficha consigo misma' using errcode = 'UF004';
  end if;

  -- Las dos filas se bloquean siempre en el mismo orden (por id): dos uniones cruzadas a la vez no se traban
  -- entre sí; la segunda espera y, al obtener el lock, encuentra una ficha ya anonimizada.
  perform 1
    from public.end_customers ec
   where ec.tenant_id = v_tenant and ec.id in (p_principal, p_absorbida)
   order by ec.id
     for update;

  select * into v_p from public.end_customers ec where ec.tenant_id = v_tenant and ec.id = p_principal;
  if not found then
    raise exception 'ficha inexistente' using errcode = 'UF001';
  end if;
  select * into v_a from public.end_customers ec where ec.tenant_id = v_tenant and ec.id = p_absorbida;
  if not found then
    raise exception 'ficha inexistente' using errcode = 'UF001';
  end if;
  if v_p.deleted_at is not null or v_a.deleted_at is not null then
    raise exception 'una de las fichas ya está anonimizada' using errcode = 'UF002';
  end if;

  -- El canal que una ficha todavía guarda en las columnas viejas (channel, channel_user_id) pasa a canales_cliente
  -- si no estaba: así no se pierde al anonimizar la absorbida y el chequeo de conflicto lo ve.
  insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at, created_at)
  select ec.tenant_id, ec.id, ec.channel, ec.channel_user_id, ec.created_at, ec.created_at
    from public.end_customers ec
   where ec.tenant_id = v_tenant and ec.id in (p_principal, p_absorbida)
     and ec.channel is not null and ec.channel_user_id is not null
  on conflict (tenant_id, canal, identificador) do nothing;

  -- Mismo tipo de canal con identificadores distintos (dos cuentas de Telegram, dos emails): no se decide solo
  -- cuál es el de la persona; se rechaza para que lo resuelva el negocio.
  if exists (
    select 1
      from public.canales_cliente p
      join public.canales_cliente a
        on a.tenant_id = p.tenant_id and a.canal = p.canal and a.identificador <> p.identificador
     where p.tenant_id = v_tenant and p.end_customer_id = p_principal and a.end_customer_id = p_absorbida
  ) then
    raise exception 'las fichas tienen el mismo canal con identificadores distintos' using errcode = 'UF003';
  end if;

  update public.canales_cliente cc set end_customer_id = p_principal
   where cc.tenant_id = v_tenant and cc.end_customer_id = p_absorbida;
  get diagnostics v_canales = row_count;

  update public.bookings b set end_customer_id = p_principal
   where b.tenant_id = v_tenant and b.end_customer_id = p_absorbida;
  get diagnostics v_turnos = row_count;

  update public.booking_packs bp set end_customer_id = p_principal
   where bp.tenant_id = v_tenant and bp.end_customer_id = p_absorbida;
  get diagnostics v_packs = row_count;

  update public.notification_outbox o set end_customer_id = p_principal
   where o.tenant_id = v_tenant and o.end_customer_id = p_absorbida;
  get diagnostics v_avisos = row_count;

  update public.notifications_log nl set end_customer_id = p_principal
   where nl.tenant_id = v_tenant and nl.end_customer_id = p_absorbida;
  get diagnostics v_registros = row_count;

  -- La principal conserva su email y su teléfono; si le faltan, toma los de la absorbida. El email de la
  -- absorbida se libera primero: la unicidad por tenant (sin distinguir mayúsculas) incluye a las dos filas.
  v_email_descartado := v_p.email is not null and v_a.email is not null;
  v_telefono_descartado := v_p.phone is not null and v_a.phone is not null;
  update public.end_customers ec set email = null where ec.tenant_id = v_tenant and ec.id = p_absorbida;
  update public.end_customers ec
     set email = coalesce(ec.email, v_a.email),
         phone = coalesce(ec.phone, v_a.phone),
         name = coalesce(ec.name, v_a.name),
         extra_data = v_a.extra_data || ec.extra_data
   where ec.tenant_id = v_tenant and ec.id = p_principal;

  -- Con los turnos, canales y avisos ya movidos, la anonimización no cancela ni borra nada de la persona.
  perform public.anonymize_end_customer_core(p_absorbida, p_actor_type, p_actor_id);

  -- Constancia sin datos personales: ids, conteos y qué datos de la absorbida no se conservaron.
  perform public.audit_log_append(
    v_tenant, p_actor_type, p_actor_id, 'customers_merged', 'end_customers', p_principal,
    jsonb_build_object(
      'absorbida_id', p_absorbida,
      'turnos', v_turnos, 'packs', v_packs, 'canales', v_canales,
      'avisos', v_avisos, 'registros_envio', v_registros,
      'email_descartado', v_email_descartado, 'telefono_descartado', v_telefono_descartado));

  return jsonb_build_object(
    'turnos', v_turnos, 'packs', v_packs, 'canales', v_canales, 'avisos', v_avisos, 'registros_envio', v_registros);
end
$$;

revoke all on function public.unir_end_customers_core(uuid, uuid, text, uuid)
  from public, anon, authenticated, service_role;

-- Función pública. El actor se deriva del rol real que llamó (session_user), nunca de un parámetro, igual que en
-- anonymize_end_customer: un rol que no es de aplicación no puede unir fichas.
create function public.unir_end_customers(p_principal uuid, p_absorbida uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor_type text := public.audit_actor_type(session_user);
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
begin
  if v_actor_type is null then
    raise exception 'rol no autorizado para unir fichas: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  return public.unir_end_customers_core(p_principal, p_absorbida, v_actor_type, v_actor_id);
end
$$;

revoke all on function public.unir_end_customers(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function public.unir_end_customers(uuid, uuid) to dashboard_service;
