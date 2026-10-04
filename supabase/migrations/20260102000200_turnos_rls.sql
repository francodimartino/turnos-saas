-- Migración de dominio 2: RLS, policies y grants de las tablas de turnos.
-- Ver ARCHITECTURE.md §3.1 (RLS), §3.2 (grants por rol), §3.3 (tests) y §7.1.
--
-- Deja las 7 tablas de turnos en "denegado por defecto", igual que la migración de plataforma 3:
--   1. revoca los grants por defecto a PUBLIC/anon/authenticated/service_role (y a los roles de
--      login, para partir de cero y otorgar explícito);
--   2. activa RLS (sin FORCE: el dueño de las funciones SECURITY DEFINER sigue operando);
--   3. crea la policy tenant_isolation;
--   4. otorga a cada rol de login exactamente lo de §3.2.
--
-- Los triggers de la migración de dominio 1 corren como invocador (no son SECURITY DEFINER), así
-- que los roles que escriben necesitan, además de lo que escriben:
--   * SELECT sobre professionals (forzar_tenant_id_desde_profesional) y sobre end_customers
--     (booking_packs_forzar_tenant_id; ya lo otorga la migración de plataforma 3);
--   * SELECT y UPDATE(used_sessions) sobre booking_packs (bookings_validar_pack y
--     bookings_devolver_sesion_pack, que devuelve la sesión al cancelar);
--   * SELECT sobre services y professional_services (validar y proteger la seña).
-- Un trigger que lee una tabla bajo RLS solo ve las filas de su tenant: un padre de otro tenant
-- aparece como inexistente y la escritura falla con foreign_key_violation.

-- ---------------------------------------------------------------------------
-- 1. Grants por defecto: fuera
-- ---------------------------------------------------------------------------
revoke all on table
  public.professionals, public.availability_rules, public.availability_blocks, public.services,
  public.professional_services, public.bookings, public.booking_packs
  from public, anon, authenticated, service_role,
       bot_service, dashboard_service, platform_admin, platform_admin_readonly, purge_job, scheduler_job;

-- ---------------------------------------------------------------------------
-- 2. RLS activada en las 7 tablas de turnos (§3.1.1)
-- ---------------------------------------------------------------------------
alter table public.professionals          enable row level security;
alter table public.availability_rules     enable row level security;
alter table public.availability_blocks    enable row level security;
alter table public.services               enable row level security;
alter table public.professional_services  enable row level security;
alter table public.bookings               enable row level security;
alter table public.booking_packs          enable row level security;

-- ---------------------------------------------------------------------------
-- 3. Policies de aislamiento por tenant (§3.1.3)
-- nullif: en una conexión reutilizada el GUC vale '' (no NULL) y ''::uuid lanzaría error.
-- platform_admin_readonly bypassea el chequeo de tenant por current_user (impersonación, §3.2);
-- solo tiene SELECT, así que el bypass nunca alcanza una escritura.
-- ---------------------------------------------------------------------------
create policy tenant_isolation on public.professionals
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.availability_rules
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.availability_blocks
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.services
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.professional_services
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.bookings
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.booking_packs
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

-- ---------------------------------------------------------------------------
-- 4. Grants por rol (§3.2)
-- ---------------------------------------------------------------------------

-- bot_service: catálogo y disponibilidad de solo lectura; bookings sin DELETE; en booking_packs
-- solo consume/devuelve sesiones (UPDATE de la columna used_sessions).
grant select on
  public.services, public.professionals, public.professional_services,
  public.availability_rules, public.availability_blocks, public.booking_packs
  to bot_service;
grant select, insert, update on public.bookings to bot_service;
grant update (used_sessions) on public.booking_packs to bot_service;

-- dashboard_service: CRUD, salvo DELETE en bookings/booking_packs (se cancelan, no se borran).
grant select, insert, update, delete on
  public.professionals, public.services, public.availability_rules,
  public.availability_blocks, public.professional_services
  to dashboard_service;
grant select, insert, update on public.bookings, public.booking_packs to dashboard_service;

-- platform_admin_readonly: solo SELECT (impersonación).
grant select on
  public.professionals, public.services, public.availability_rules, public.availability_blocks,
  public.professional_services, public.bookings, public.booking_packs
  to platform_admin_readonly;

-- platform_admin, purge_job y scheduler_job: sin grants sobre las tablas de turnos.
-- (purge_job y scheduler_job operan solo vía funciones SECURITY DEFINER.)
