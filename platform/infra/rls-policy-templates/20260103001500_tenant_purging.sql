-- Migración de plataforma: estado intermedio 'purging' en tenants.status y gracia mínima de 30 días en la base.
--
-- Problema (carrera purga/reactivación): la purga corre en varios pasos con commits en el medio (el job elimina
-- las cuentas de Auth entre purge_tenant_data y mark_tenant_purged). Mientras tanto el tenant seguía 'inactive',
-- así que un platform_admin podía reactivarlo (inactive -> active) con los datos ya anonimizados y las cuentas
-- borradas: un tenant activo, vacío e irrecuperable. Además el período de gracia se podía bajar a 1 día (o a algo
-- que no es un número) y la base lo aceptaba.
--
-- Solución:
--   1. tenants.status admite 'purging'. Ciclo: inactive -> purging -> purged. Desde purging NO se vuelve atrás:
--      purging -> active / inactive / suspended se rechazan con check_violation (23514). inactive -> purged directo
--      tampoco existe más: hay que pasar por purging, que es lo que toma el lock y re-valida la gracia.
--   2. begin_tenant_purge(tenant_id) hace inactive -> purging bajo `for update`, re-validando en la base que el
--      tenant esté inactive con la gracia vencida. Es idempotente sobre un tenant que ya está purging.
--      Una reactivación concurrente queda esperando el lock y, al obtenerlo, el trigger ve purging y la rechaza.
--   3. platform_settings: un trigger rechaza guardar 'tenant_deletion_grace_period_days' menor a 30 (o que no sea
--      un entero), y list_tenants_due_for_purge ignora, para los inactive, un valor menor a 30 que ya estuviera
--      guardado. El valor sembrado (90) sigue siendo válido.
--   4. list_tenants_due_for_purge también lista los purging, para retomar una purga interrumpida.
--   5. mark_tenant_purged parte de purging (purge_tenant_data, en la migración 20260103001600, también).
--
-- INTERFAZ PARA EL BACKEND (tarea siguiente: job de purga y panel de platform_admin)
--   Flujo del job, todo como purge_job (solo él ejecuta estas funciones):
--     1. select * from public.list_tenants_due_for_purge()        -> (tenant_id, days_overdue); incluye inactive
--                                                                    vencidos Y purging (purga a retomar).
--     2. select public.begin_tenant_purge(:tenant_id)              -> void. check_violation (23514) si el tenant no
--                                                                    está inactive/purging o la gracia no venció;
--                                                                    P0002 si no existe. Idempotente en purging.
--     3. paso 0 (calendar_scrub_targets), purge_tenant_data(:tenant_id), borrar cuentas de Auth,
--        mark_tenant_purged(:tenant_id) -> sin cambios de firma. Por compatibilidad purge_tenant_data y
--        mark_tenant_purged, si reciben un tenant inactive vencido, llaman ellos mismos a begin_tenant_purge.
--   Panel (platform_admin): un tenant en 'purging' no se puede reactivar, suspender ni dar de baja: el UPDATE de
--   status falla con check_violation (23514) y el backend debe mostrarlo como "purga en curso" (409), no como 500.
--   Guardar el período de gracia con menos de 30 falla con check_violation (23514); el backend debe validar >= 30
--   antes y traducir el error. 'purging' no tiene acceso (auth hook, resolve_inbound_tenant y policies solo dejan
--   pasar status = 'active').

-- ---------------------------------------------------------------------------
-- 1. tenants.status admite 'purging' (ensancha el check: no toca datos existentes)
-- ---------------------------------------------------------------------------
do $$
declare
  v_nombre text;
begin
  select c.conname into v_nombre
    from pg_constraint c
   where c.conrelid = 'public.tenants'::regclass and c.contype = 'c'
     and pg_get_constraintdef(c.oid) like '%''inactive''%''purged''%'
     and pg_get_constraintdef(c.oid) not like '%deactivated_at%';
  if v_nombre is null then
    raise exception 'no se encontró el check de tenants.status';
  end if;
  execute format('alter table public.tenants drop constraint %I', v_nombre);
end
$$;

alter table public.tenants
  add constraint tenants_status_check
  check (status in ('active', 'suspended', 'inactive', 'purging', 'purged'));

-- un purging sin fecha de baja no tendría de dónde contar la gracia ya vencida
alter table public.tenants
  add constraint tenants_purging_requiere_deactivated_at
  check (status <> 'purging' or deactivated_at is not null);

-- ---------------------------------------------------------------------------
-- 2. Transiciones (§2.2): active <-> suspended, active/suspended -> inactive, inactive -> active,
--    inactive -> purging -> purged. purged es terminal; purging solo avanza.
-- ---------------------------------------------------------------------------
create or replace function public.tenants_validar_transicion_status()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.status is not distinct from old.status then
    return new;
  end if;

  if not (
    (old.status = 'active'    and new.status in ('suspended', 'inactive'))
    or (old.status = 'suspended' and new.status in ('active', 'inactive'))
    or (old.status = 'inactive'  and new.status in ('active', 'purging'))
    or (old.status = 'purging'   and new.status = 'purged')
  ) then
    raise exception 'Transición de estado de tenant inválida: % -> %', old.status, new.status
      using errcode = 'check_violation';
  end if;

  if new.status = 'inactive' then
    -- arranca el reloj de purga
    new.deactivated_at := coalesce(new.deactivated_at, now());
  elsif new.status in ('active', 'suspended') then
    -- reactivar (o suspender) nunca deja un reloj de purga corriendo
    new.deactivated_at := null;
  end if;

  return new;
end
$$;

revoke all on function public.tenants_validar_transicion_status() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. Gracia mínima de 30 días en la base
-- ---------------------------------------------------------------------------
create function public.platform_settings_validar_gracia()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.key = 'tenant_deletion_grace_period_days' then
    if jsonb_typeof(new.value) is distinct from 'number'
       or (new.value #>> '{}') !~ '^[0-9]{2,5}$'
       or (new.value #>> '{}')::integer < 30 then
      raise exception 'tenant_deletion_grace_period_days debe ser un entero de al menos 30 días'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;

revoke all on function public.platform_settings_validar_gracia() from public, anon, authenticated, service_role;

create trigger platform_settings_validar_gracia
  before insert or update on public.platform_settings
  for each row execute function public.platform_settings_validar_gracia();

-- ---------------------------------------------------------------------------
-- 4. list_tenants_due_for_purge: inactive con la gracia vencida (gracia efectiva >= 30) + purging
-- ---------------------------------------------------------------------------
-- El período sale de platform_settings; si la fila falta, no es un entero o es menor a 30, no vence ningún
-- inactive (nunca se interpreta "sin configurar" como 0 días). Los purging figuran siempre: la purga ya empezó
-- y hay que terminarla; su atraso es 0 si la gracia actual todavía no hubiera vencido.
create or replace function public.list_tenants_due_for_purge()
returns table (tenant_id uuid, days_overdue integer)
language sql
stable
security definer
set search_path = ''
as $$
  with cfg as (
    select case
             when jsonb_typeof(ps.value) = 'number' and (ps.value #>> '{}') ~ '^[0-9]{1,5}$'
                  and (ps.value #>> '{}')::integer >= 30
               then (ps.value #>> '{}')::integer
           end as grace_days
      from public.platform_settings ps
     where ps.key = 'tenant_deletion_grace_period_days'
  )
  select t.id,
         case when cfg.grace_days is null then 0
              else greatest(
                     floor(extract(epoch from (now() - (t.deactivated_at + make_interval(days => cfg.grace_days)))) / 86400)::integer,
                     0)
         end
    from public.tenants t
    left join cfg on true
   where t.deactivated_at is not null
     and (
       t.status = 'purging'
       or (t.status = 'inactive'
           and cfg.grace_days is not null
           and t.deactivated_at + make_interval(days => cfg.grace_days) <= now())
     )
   order by t.deactivated_at, t.id
$$;

revoke all on function public.list_tenants_due_for_purge() from public, anon, authenticated, service_role;
grant execute on function public.list_tenants_due_for_purge() to purge_job;

-- ---------------------------------------------------------------------------
-- 5. begin_tenant_purge: inactive -> purging bajo lock
-- ---------------------------------------------------------------------------
create function public.begin_tenant_purge(p_tenant_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
begin
  -- el lock hace que una reactivación concurrente espere; al obtenerlo, el trigger ya ve purging y la rechaza
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status = 'purging' then
    return;
  end if;
  if v_status <> 'inactive' then
    raise exception 'purga rechazada: el tenant no está inactive' using errcode = 'check_violation';
  end if;
  if not exists (select 1 from public.list_tenants_due_for_purge() l where l.tenant_id = p_tenant_id) then
    raise exception 'purga rechazada: el período de gracia no venció' using errcode = 'check_violation';
  end if;

  update public.tenants t set status = 'purging' where t.id = p_tenant_id;

  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'begin_tenant_purge'));
end
$$;

revoke all on function public.begin_tenant_purge(uuid) from public, anon, authenticated, service_role;
grant execute on function public.begin_tenant_purge(uuid) to purge_job;

-- ---------------------------------------------------------------------------
-- 6. mark_tenant_purged: parte de purging (un inactive vencido pasa antes por begin_tenant_purge)
-- ---------------------------------------------------------------------------
create or replace function public.mark_tenant_purged(p_tenant_id uuid)
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
  if v_status = 'inactive' then
    -- valida inactive + gracia vencida bajo lock y pasa a purging (o rechaza)
    perform public.begin_tenant_purge(p_tenant_id);
  elsif v_status <> 'purging' then
    raise exception 'purga rechazada: el tenant no está inactive ni en purga' using errcode = 'check_violation';
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
