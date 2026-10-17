-- Quita el bypass `or current_user = 'platform_admin_readonly'` de las policies tenant_isolation.
--
-- Hasta ahora platform_admin_readonly (impersonacion de solo lectura) veia todos los tenants por current_user.
-- Desde que readonly_tx exige y fija app.current_tenant_id, el rol queda sujeto al mismo aislamiento que
-- dashboard_service: sin contexto de tenant ve 0 filas y con contexto ve solo las del tenant fijado.
-- `tenants` conserva su policy por id: el rol lee la fila del tenant del contexto y ninguna otra.
-- alter policy conserva roles y comando; solo cambia la expresion using. No toca datos.

alter policy tenant_isolation on public.tenant_users
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.tenant_user_permissions
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.tenant_integrations
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.end_customers
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.audit_log
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.inbound_messages
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.bot_sessions
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.notifications_log
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.payments
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.professionals
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.availability_rules
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.availability_blocks
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.services
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.professional_services
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.bookings
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.booking_packs
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

alter policy tenant_isolation on public.tenants
  using (id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
