"""SQL de la web pública (rol `web_reservas`): todo parametrizado, nunca armado con texto del cliente.

El rol solo lee de `tenants` las columnas `id`, `name` y `settings`; de `settings` se piden una por una las claves
públicas (ver `negocio.CLAVES_PUBLICAS_DE_SETTINGS`), con su tipo comprobado, y nunca el objeto entero.
"""

# Único acceso sin contexto de tenant. Solo devuelve tenants activos: suspendido, dado de baja, purgado e inexistente
# no se distinguen.
SQL_RESOLVER_SLUG = "select tenant_id, nombre, slug_vigente, reemplazado from public.resolver_slug_tenant($1)"

SQL_NEGOCIO = """
select name as nombre,
       case when jsonb_typeof(settings -> 'local_direccion') = 'string'
            then settings ->> 'local_direccion' end as direccion,
       case when jsonb_typeof(settings -> 'color_principal') = 'string'
            then settings ->> 'color_principal' end as color_principal,
       case when jsonb_typeof(settings -> 'color_secundario') = 'string'
            then settings ->> 'color_secundario' end as color_secundario,
       case when jsonb_typeof(settings -> 'logo_ruta') = 'string'
            then settings ->> 'logo_ruta' end as logo_ruta
from public.tenants
where id = $1
"""

# Un servicio solo se ofrece si algún profesional activo lo atiende en un modo reservable (`$2`). El tope acota la
# respuesta de un negocio con un catálogo desmedido.
TOPE_FILAS_CATALOGO = 2000
SQL_CATALOGO = """
select s.id as servicio_id, s.name as servicio, s.duration_minutes, s.price,
       p.id as profesional_id, p.name as profesional
from public.professional_services ps
join public.services s on s.id = ps.service_id
join public.professionals p on p.id = ps.professional_id
where ps.tenant_id = $1 and s.tenant_id = $1 and p.tenant_id = $1
  and s.is_active and p.is_active and ps.confirmation_mode = any($2::text[])
order by s.name, s.id, p.name, p.id
limit 2000
"""

SQL_PAR_RESERVABLE = """
select 1 as existe
from public.professional_services ps
join public.services s on s.id = ps.service_id
join public.professionals p on p.id = ps.professional_id
where ps.tenant_id = $1 and ps.service_id = $2 and ps.professional_id = $3
  and s.tenant_id = $1 and p.tenant_id = $1
  and s.is_active and p.is_active and ps.confirmation_mode = any($4::text[])
"""

SQL_AVISO_MINIMO = "select settings ->> 'booking_min_notice_minutes' as aviso_minimo from public.tenants where id = $1"
