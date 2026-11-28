-- Privilegios del rol de la web de reservas sobre lo que crea la migración de sesiones y códigos de la web
-- (sesiones_web, codigos_web y las columnas de la política de privacidad de end_customers).
-- El rol se crea en 20260103004100_roles_web_y_enviador.sql, antes de que existieran estas tablas y columnas.
-- Migración aditiva: solo otorga privilegios.
--
-- Por columna y sin las que pone la base: created_at (marca de creación que fija el default now(); los topes por hora
-- y el vencimiento se calculan contra ella, así que la web no la escribe), id (lo genera la base), y en codigos_web
-- tampoco intentos ni usado_at al insertar (nacen en su valor inicial).
--     sesiones_web  SELECT; INSERT (tenant_id, end_customer_id, token_hash, expira_at); UPDATE (revocada_at)
--     codigos_web   SELECT; INSERT (tenant_id, email_hash, codigo_hash, expira_at, ip_hash);
--                   UPDATE (intentos, usado_at)
--     end_customers SELECT, INSERT y UPDATE de (politica_version, politica_aceptada_at)
-- Ni DELETE en ninguna: las sesiones se revocan, y los triggers de anonimización de una ficha y de purga de un negocio
-- borran las sesiones (y, en la purga, los códigos).

grant select on public.sesiones_web, public.codigos_web to web_reservas;

grant insert (tenant_id, end_customer_id, token_hash, expira_at) on public.sesiones_web to web_reservas;
grant update (revocada_at) on public.sesiones_web to web_reservas;

grant insert (tenant_id, email_hash, codigo_hash, expira_at, ip_hash) on public.codigos_web to web_reservas;
grant update (intentos, usado_at) on public.codigos_web to web_reservas;

grant select (politica_version, politica_aceptada_at) on public.end_customers to web_reservas;
grant insert (politica_version, politica_aceptada_at) on public.end_customers to web_reservas;
grant update (politica_version, politica_aceptada_at) on public.end_customers to web_reservas;
