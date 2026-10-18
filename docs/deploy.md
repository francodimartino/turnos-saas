# Despliegue a producción

Cómo se despliega el producto y qué configura cada servicio. La configuración versionada es `render.yaml` (Render:
3 web services y 2 Cron Jobs) y `apps/*-frontend/vercel.json` (Vercel). Acá están las variables de cada servicio, de
dónde sale cada valor y el orden del primer deploy. **Nunca se versionan valores**: los secretos se cargan en el
panel de cada proveedor (o con `gh secret set` / la CLI, que piden el valor sin dejarlo en el historial).

## Principios

- **El deploy no es automático.** Ni Render (`autoDeploy: false` en cada servicio) ni Vercel (`git.deploymentEnabled`
  de `main` en `false`) despliegan al mergear: un merge a `main` no cambia producción. El deploy se dispara a mano,
  después de revisar, desde el panel o la CLI del proveedor (ver "Desplegar y revertir").
- **Un servicio, un rol de base de datos**, con el mínimo privilegio (tabla de abajo). Ningún servicio conecta como
  el dueño de las migraciones.
- **Todo lo que depende del dominio es una variable a completar.** El dominio todavía no está definido; hasta que lo
  esté, no se puede hacer el primer deploy completo (ver "Dominio y cookies").

## Servicios y roles de conexión

| Servicio (Render) | Tipo / plan | Arranque | Rol de Postgres | Variable de conexión |
|---|---|---|---|---|
| `turnos-bot` | web, Starter (siempre encendido) | `uvicorn bot_app.main:app` | `bot_service` | `BOT_DATABASE_URL` |
| `turnos-dashboard-backend` | web, Free | `uvicorn dashboard_backend.main:crear_app_desde_entorno --factory` | `dashboard_service` | `DASHBOARD_DATABASE_URL` |
| `turnos-admin-backend` | web, Free | `uvicorn admin_backend.main:crear_app_desde_entorno --factory` | `platform_admin` y `platform_admin_readonly` | `ADMIN_DATABASE_URL`, `ADMIN_READONLY_DATABASE_URL` |
| `turnos-job-purga` | cron, Starter, `0 6 * * *` (06:00 UTC = 03:00 Argentina) | `python -m jobs_purga` | `purge_job` | `PURGE_DATABASE_URL` |
| `turnos-job-vencimiento` | cron, Starter, `*/15 * * * *` | `python -m jobs_vencimiento` | `scheduler_job` | `SCHEDULER_DATABASE_URL` |

Build de todos: `pip install -r requirements.lock && pip install --no-deps .` (primero las dependencias exactas del lock, después los paquetes locales sin resolver nada de nuevo). Los web
services exponen `GET /health` (200 solo si hay conexión a la base; el bot además comprueba que el rol sea
`bot_service`) y Render lo usa como health check. Región `virginia`: Render no tiene São Paulo (donde está
Supabase), es la más cercana.

El horario de la purga (06:00 UTC) está en tres lugares que tienen que coincidir: `schedule` del Cron Job,
`PURGA_CRON` (el monitor de cron de Sentry) y `CRON_POR_DEFECTO` de `apps/jobs/jobs_purga/proceso.py`. Un test lo
verifica (`tests/test_despliegue.py`).

### Cadena de conexión (pooler de Supabase en modo transacción)

Render no tiene salida IPv6, y la conexión directa de Supabase es solo IPv6 en el plan Free: los servicios usan el
**pooler en modo transacción** (Supabase > Connect > Transaction pooler, puerto 6543). Dos detalles:

- El usuario del pooler es `<rol>.<ref-del-proyecto>`, por ejemplo `bot_service.tpbvxikgynwqvwsrjuyx`. El bot acepta
  ese formato (valida el prefijo y, además, comprueba el rol real en la base al arrancar y en cada `/health`).
- El código ya usa `statement_cache_size=0`, que el pooler en modo transacción exige (el bot y los jobs lo fijan
  solos; en `dashboard-backend` y `admin-backend` lo fija `DB_STATEMENT_CACHE_SIZE=0`, ya puesto en el Blueprint).

Formato: `postgresql://<rol>.<ref>:<contraseña>@<host-del-pooler>:6543/postgres`. La contraseña es la que se asignó
al rol en el paso 2 del primer deploy; si tiene caracteres especiales va codificada como URL.

## Variables de entorno por servicio

"Secreto" = va con `sync: false` en `render.yaml` y se carga en el panel de Render. Los valores no secretos que no
dependen del proyecto ni del dominio están fijos en el Blueprint.

### Compartidas (varios servicios)

| Variable | Para qué | De dónde sale | Secreto | Servicios |
|---|---|---|---|---|
| `PYTHON_VERSION` | Versión de Python de Render (`3.12.8`; el proyecto exige 3.11+) | Fija en el Blueprint | No | todos |
| `SUPABASE_URL` | URL del proyecto: `https://tpbvxikgynwqvwsrjuyx.supabase.co` | Se carga en el panel de Render (Supabase > Settings > API) | No (va con `sync: false` para que el Blueprint no quede atado a un proyecto) | dashboard-backend, admin-backend, job-purga |
| `SUPABASE_ANON_KEY` | Clave anónima: la usa el proxy de login/MFA | Supabase > Settings > API Keys (`anon` / publishable) | Sí (aunque es pública por diseño, no se versiona) | dashboard-backend, admin-backend |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role`: solo para la Auth Admin API (invitar usuarios, eliminar cuentas en la purga). **No va al bot ni al job de vencimiento.** | Supabase > Settings > API Keys (`service_role` / secret) | Sí. **Una clave `sb_secret_…` distinta por servicio** (misma variable, valor distinto; ver "Claves de la Auth Admin API: una por servicio") | dashboard-backend (invitar al staff), admin-backend (invitar al owner de un tenant nuevo), job-purga |
| `SENTRY_DSN` | DSN del proyecto de Sentry del servicio; vacío = Sentry apagado | Sentry (org `nesso-jx`) > proyecto > Client Keys. Un proyecto por servicio: `turnos-dashboard-backend`, `turnos-admin-backend`, `turnos-bot`, y `turnos-jobs` para los dos Cron Jobs | Sí (no se versiona) | todos |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Cliente OAuth de Google Calendar (mismo para los tres servicios) | Google Cloud Console > Credenciales | `CLIENT_SECRET`: sí. `CLIENT_ID`: no, pero se carga igual por panel | bot, dashboard-backend, job-purga |
| `GOOGLE_OAUTH_REDIRECT_URI` | URL de retorno de OAuth: `<DASHBOARD_ORIGIN>/calendar/callback`. Misma que se registra en Google. | **Depende del dominio** | No | bot, dashboard-backend, job-purga |
| `DB_STATEMENT_CACHE_SIZE` | `0` para el pooler en modo transacción | Fija en el Blueprint | No | dashboard-backend, admin-backend |
| `LOGIN_IP_HEADER` | Header del que se toma la IP real del cliente para el límite de intentos de login: `CF-Connecting-IP` (Cloudflare en modo proxy) o `X-Forwarded-For`. **Obligatoria en producción**: sin ella se usa la IP del socket, que detrás de Cloudflare/Render es la del proxy, y el límite por IP cuenta a todos los usuarios juntos. Cualquier otro valor hace fallar el arranque. Solo es confiable si el acceso directo a `*.onrender.com` está bloqueado (ver el checklist) | Se carga en el panel de Render (`sync: false`) | No | dashboard-backend, admin-backend |
| `LOGIN_PROXIES_CONFIABLES` | Con `X-Forwarded-For`: cuántos proxies propios hay delante de la app (entero >= 1, por defecto 1). No se usa con `CF-Connecting-IP` | Opcional, no está en el Blueprint | No | dashboard-backend, admin-backend |
| `LOGIN_LIMITE_IP`, `LOGIN_LIMITE_EMAIL`, `LOGIN_VENTANA_SEG` | Límite de intentos de login por IP (20), por email (5) y tamaño de la ventana en segundos (900). Un valor no numérico o menor que 1 se ignora y se usa el defecto | Opcionales, no están en el Blueprint | No | dashboard-backend, admin-backend |
| `TURNSTILE_SECRET_KEY` | Clave secreta de Cloudflare Turnstile (captcha del login). Vacía = Turnstile apagado. **Se activa en par** con `VITE_TURNSTILE_SITE_KEY` del frontend correspondiente (ver "Frontends"): si el backend tiene la secret y el frontend no tiene la site key, el frontend no manda token y **nadie puede iniciar sesión** | Cloudflare > Turnstile > widget (un widget por frontend; la secret va al backend de ese frontend) | Sí (`sync: false`) | dashboard-backend, admin-backend |
| `HTTP_MAX_BODY_BYTES` | Límite de tamaño del body (413 si se supera). Por defecto 1048576 (1 MiB); un valor inválido o <= 0 cae al defecto | Opcional, no está en el Blueprint | No | bot, dashboard-backend, admin-backend |

Sin las tres variables de Google compartidas (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`) y, en el dashboard-backend, `CALENDAR_OAUTH_STATE_SECRET`, conectar calendarios responde 503 en el dashboard y el paso de calendario de la
purga registra `calendar_scrub_failed` (la purga sigue). Se necesitan antes de que el primer tenant conecte su
calendario, no para arrancar.

### turnos-bot

| Variable | Para qué | De dónde sale | Secreto |
|---|---|---|---|
| `BOT_DATABASE_URL` | Conexión como `bot_service` (el bot se niega a arrancar con otro rol) | Pooler, rol `bot_service` | Sí |
| `SENTRY_DSN`, `GOOGLE_OAUTH_*`, `HTTP_MAX_BODY_BYTES` | Ver "Compartidas" | | |

El token del bot de Telegram y el `webhook_secret` de cada tenant **no son variables de entorno**: viven cifrados en
el Vault de Supabase (se cargan desde el panel admin) y el bot los lee con su rol.

### turnos-dashboard-backend

| Variable | Para qué | De dónde sale | Secreto |
|---|---|---|---|
| `DASHBOARD_DATABASE_URL` | Conexión como `dashboard_service` | Pooler, rol `dashboard_service` | Sí |
| `DASHBOARD_ORIGIN` | Único origen del dashboard-frontend (CORS y comprobación de origen): `https://app.<dominio>`, sin barra ni ruta | **Depende del dominio** | No |
| `SUPABASE_JWKS_URL` | Verificación de JWT con claves asimétricas: `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, o sea `https://tpbvxikgynwqvwsrjuyx.supabase.co/auth/v1/.well-known/jwks.json` | Se carga en el panel de Render | No (`sync: false`) |
| `SUPABASE_JWT_SECRET` | Alternativa a `SUPABASE_JWKS_URL` (HS256) si el proyecto no usa claves asimétricas. **Una u otra, nunca las dos**: dejar vacía `SUPABASE_JWKS_URL` (o quitarla del Blueprint) y cargar esta | Supabase > Settings > JWT | Sí |
| `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER` | Opcionales: audiencia (por defecto `authenticated`) y emisor esperado (`https://tpbvxikgynwqvwsrjuyx.supabase.co/auth/v1`) | No están en el Blueprint; se agregan si se quiere validar el emisor | No |
| `COOKIE_SECURE` | `true` (fija) | Blueprint | No |
| `COOKIE_SAMESITE` | `strict` (fija); `none` no se admite | Blueprint | No |
| `COOKIE_REFRESH_MAX_AGE` | Vida de la cookie del refresh token en segundos; opcional (7 días) | No está en el Blueprint | No |
| `CALENDAR_OAUTH_STATE_SECRET` | Clave HMAC que firma el `state` de OAuth (mínimo 16 caracteres). Es de este servicio, que inicia y completa OAuth (firma el `state`, atado al usuario y de un solo uso); el bot y el job de purga no la llevan ni la necesitan | Generada al azar: `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Sí |
| `LOGIN_IP_HEADER`, `LOGIN_PROXIES_CONFIABLES`, `LOGIN_LIMITE_IP`, `LOGIN_LIMITE_EMAIL`, `LOGIN_VENTANA_SEG`, `TURNSTILE_SECRET_KEY`, `HTTP_MAX_BODY_BYTES` | Ver "Compartidas" | | |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SENTRY_DSN`, `GOOGLE_OAUTH_*`, `DB_STATEMENT_CACHE_SIZE` | Ver "Compartidas" | | |

### turnos-admin-backend

| Variable | Para qué | De dónde sale | Secreto |
|---|---|---|---|
| `ADMIN_DATABASE_URL` | Conexión como `platform_admin` | Pooler, rol `platform_admin` | Sí |
| `ADMIN_READONLY_DATABASE_URL` | Conexión como `platform_admin_readonly` (solo SELECT): impersonación de solo lectura | Pooler, rol `platform_admin_readonly` | Sí |
| `ADMIN_ORIGIN` | Único origen del admin-frontend: `https://admin.<dominio>` | **Depende del dominio** | No |
| `SUPABASE_JWKS_URL` / `SUPABASE_JWT_SECRET`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER`, `COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_REFRESH_MAX_AGE` | Igual que en el dashboard-backend | | |
| `LOGIN_IP_HEADER`, `LOGIN_PROXIES_CONFIABLES`, `LOGIN_LIMITE_IP`, `LOGIN_LIMITE_EMAIL`, `LOGIN_VENTANA_SEG`, `TURNSTILE_SECRET_KEY`, `HTTP_MAX_BODY_BYTES` | Ver "Compartidas" | | |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SENTRY_DSN`, `DB_STATEMENT_CACHE_SIZE` | Ver "Compartidas" | | |

### turnos-job-purga

| Variable | Para qué | De dónde sale | Secreto |
|---|---|---|---|
| `PURGE_DATABASE_URL` | Conexión como `purge_job` (sin grants sobre tablas, solo funciones) | Pooler, rol `purge_job` | Sí |
| `PURGA_CRON` | Horario crontab UTC del monitor de cron de Sentry (`0 6 * * *`): igual al `schedule` | Fija en el Blueprint | No |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SENTRY_DSN`, `GOOGLE_OAUTH_*` | Ver "Compartidas" | | |

### turnos-job-vencimiento

| Variable | Para qué | De dónde sale | Secreto |
|---|---|---|---|
| `SCHEDULER_DATABASE_URL` | Conexión como `scheduler_job` | Pooler, rol `scheduler_job` | Sí |
| `SENTRY_DSN` | Ver "Compartidas" (proyecto `turnos-jobs`) | | |

### Frontends (Vercel)

Vite las incrusta en el bundle **al compilar**: son públicas, nunca llevan secretos, y cambiarlas exige un redeploy.
Se cargan en Vercel > Settings > Environment Variables (entorno Production).

| Variable | Para qué | De dónde sale |
|---|---|---|
| `VITE_API_URL` | Origen del backend del frontend: `https://api.<dominio>` (dashboard) o `https://admin-api.<dominio>` (admin) | **Depende del dominio** |
| `VITE_SENTRY_DSN` | DSN del proyecto `turnos-dashboard-frontend` / `turnos-admin-frontend`; vacío = Sentry apagado | Sentry > Client Keys |
| `VITE_TURNSTILE_SITE_KEY` | Site key (pública) del widget de Turnstile en el login del frontend (y en aceptar invitación, en el dashboard); vacía = no se carga el widget. **Va en par con `TURNSTILE_SECRET_KEY`** del backend de ese frontend: cargar las dos a la vez, y como Vite la incrusta al compilar, hace falta redeploy del frontend | Cloudflare > Turnstile > widget (el de ese frontend) |

### Fuera de los servicios: setup inicial

`scripts/setup_proyecto.py` corre una sola vez desde la máquina de Franco, no en Render:

| Variable | Para qué | Secreto |
|---|---|---|
| `SETUP_DATABASE_URL` | Conexión como dueño de las migraciones (`postgres`): pooler en modo sesión (puerto 5432 del host del pooler, usuario `postgres.<ref>`) o conexión directa si hay IPv6 | Sí |
| `SETUP_PASSWORD_<ROL>` (`BOT_SERVICE`, `DASHBOARD_SERVICE`, `PLATFORM_ADMIN`, `PLATFORM_ADMIN_READONLY`, `PURGE_JOB`, `SCHEDULER_JOB`) | Contraseña de cada rol de login (o `--pedir-passwords`) | Sí |

## Claves de la Auth Admin API: una por servicio

Tres servicios usan la Auth Admin API de Supabase: dashboard-backend (invitar staff), admin-backend (invitar al owner
de un tenant nuevo) y job-purga (eliminar cuentas). Todos leen la misma variable, `SUPABASE_SERVICE_ROLE_KEY`, pero
**cada uno con una clave secreta propia** (`sb_secret_…`), para poder revocar la de uno sin afectar a los otros. Esa
clave habilita toda la Auth Admin API (cambiar contraseñas, borrar factores MFA, generar links de login de cualquier
usuario, incluido el admin de plataforma); el riesgo del dashboard está aceptado y explicado en `ARCHITECTURE.md` §3.7.
El bot y el job de vencimiento no llevan ninguna. En `local/` se usa una sola clave para todo (es desarrollo).

**Crear una clave por servicio.** En el panel de Supabase del proyecto, en la sección de API keys, crear tres secret
keys con nombres que identifiquen al servicio (por ejemplo `dashboard-backend`, `admin-backend`, `job-purga`). Cada
valor se copia una sola vez, directo al panel de Render del servicio correspondiente (variable
`SUPABASE_SERVICE_ROLE_KEY`) o al gestor de contraseñas; nunca por chat ni en un archivo versionado. Los nombres exactos
de las pantallas pueden cambiar: si no coinciden, buscar en la documentación de Supabase "API keys" / "secret keys".

**Rotar una clave** (periódicamente, y de inmediato ante sospecha de filtración):

1. Crear en Supabase una secret key nueva para ese servicio.
2. Cargarla en `SUPABASE_SERVICE_ROLE_KEY` del servicio en el panel de Render y redeployar solo ese servicio.
3. Comprobar que lo que usa la clave funciona (invitar a un usuario de prueba; para el job, una corrida manual).
4. Recién entonces revocar/eliminar la clave anterior de ese servicio en Supabase. Las de los otros servicios no se tocan.

Ante un compromiso confirmado del dashboard-backend: revocar su clave primero (la invitación de staff queda caída hasta
cargar una nueva) y revisar los usuarios y factores MFA recientes en Supabase Auth.

## Dominio y cookies

Las cookies de sesión son `Secure` y `SameSite=Strict`: frontend y backend tienen que ser el **mismo sitio** (mismo
dominio registrable). `*.vercel.app` y `*.onrender.com` son sitios distintos, así que **no sirven**: hace falta
dominio propio antes del primer deploy. Con un dominio `<dominio>`:

| Qué | Nombre | Apunta a |
|---|---|---|
| dashboard-frontend | `app.<dominio>` | Vercel (CNAME, solo DNS en Cloudflare) |
| admin-frontend | `admin.<dominio>` | Vercel (CNAME, solo DNS) |
| dashboard-backend | `api.<dominio>` | Render (dominio personalizado del servicio) |
| admin-backend | `admin-api.<dominio>` | Render |
| bot | `bot.<dominio>` | Render (el webhook de Telegram apunta acá) |

Valores que se derivan: `DASHBOARD_ORIGIN=https://app.<dominio>`, `ADMIN_ORIGIN=https://admin.<dominio>`,
`GOOGLE_OAUTH_REDIRECT_URI=https://app.<dominio>/calendar/callback`, `VITE_API_URL` según la tabla anterior. (Los
nombres son una propuesta; lo que importa es el dominio registrable común y que cada origen sea uno solo.) El proxy
de Cloudflare delante de los backends (WAF, rate limiting) es un paso aparte, con su propia aprobación.

### Conexión de Google Calendar

`GOOGLE_OAUTH_REDIRECT_URI` apunta a la **página del dashboard** (`https://app.<dominio>/calendar/callback`), no al
backend: esa página lee `code` y `state` de la URL y hace un `POST /calendar/callback` al dashboard-backend con la
cookie de sesión (por eso funciona con `SameSite=Strict`, sin cambiar la cookie). Solo ese POST, protegido por el
chequeo de `Origin`, completa la conexión. El `GET /calendar/callback` del backend no escribe nada ni llama a Google:
solo redirige al dashboard. El `state` va firmado, atado al usuario que inicia la conexión y es de un solo uso (un
`state` reusado o de otro usuario se rechaza como `state_invalido`).

## Supabase Auth

La configuración de Auth del proyecto hosteado tiene que coincidir con la que se prueba en local. `supabase/config.toml`
versiona la parte reproducible; el resto se carga a mano en el panel del proyecto (Authentication). Ojo con
`supabase config push`: sube también los valores locales (`site_url` y redirects de `127.0.0.1`), que en producción
serían incorrectos; si se usa, revisar el diff que muestra antes de confirmar. Lo más simple es cargar a mano y
comparar con este cuadro.

| Qué | Cómo debe quedar | Origen |
|---|---|---|
| Auth Hook `custom_access_token_hook` | Activado: Authentication > Hooks > Custom Access Token > tipo Postgres, función `public.custom_access_token_hook`. Es el que mete el tenant y el rol en el JWT; sin él, nadie tiene permisos. La función la crean las migraciones (`supabase db push`) | `config.toml` (`[auth.hook.custom_access_token]`); en el hosteado se activa a mano o con `config push` |
| Plantilla de invitación | Authentication > Emails > Invite user, asunto `Te invitaron a Turnos` y el contenido de `supabase/templates/invite.html`, que arma el enlace `{{ .SiteURL }}/invitacion?token_hash={{ .TokenHash }}&type=invite`. Usa `{{ .TokenHash }}` (el frontend lo canjea en `/invitacion`) y no el enlace de confirmación prearmado, para que un escáner de correos no consuma el enlace antes que la persona | `config.toml` (`[auth.email.template.invite]`) y el archivo; en el hosteado se copia a mano |
| Site URL | `https://app.<dominio>` (el dashboard-frontend: es el host al que apunta `{{ .SiteURL }}` en la invitación). Nunca `localhost` | **A mano** (depende del dominio) |
| URLs de redirección permitidas | `https://app.<dominio>` y `https://admin.<dominio>`, y nada más (sin comodines abiertos ni `localhost`) | **A mano** (depende del dominio) |
| MFA TOTP | Enrolar y verificar habilitados (Authentication > Multi-Factor) | `config.toml` (`[auth.mfa.totp]`) |
| Signup | **Desactivado** (`enable_signup = false`): solo se entra por invitación. Verificar también que no haya providers sociales ni login anónimo habilitados | `config.toml` |
| Política de contraseñas | Mínimo 12 caracteres, con minúsculas, mayúsculas, dígitos y símbolos | `config.toml` (`minimum_password_length`, `password_requirements`) |
| Protección de contraseñas filtradas (HaveIBeenPwned) | Activada (Authentication > Attack Protection). Depende del plan de Supabase (hoy, Pro o superior): si el plan no la ofrece, queda como riesgo aceptado hasta subir de plan | **A mano**; no se versiona |
| Rotación del refresh token y detección de reuso | Rotación activada y ventana de reuso de 10 segundos: un refresh token reusado fuera de esa ventana invalida la familia de sesiones | `config.toml` (`enable_refresh_token_rotation`, `refresh_token_reuse_interval`) |
| Duración del access token | 3600 s: el access token sigue válido hasta `exp` aunque se cierre sesión (ventana de 1 hora aceptada; bajar `jwt_expiry` la acorta) | `config.toml` (`jwt_expiry`) |
| SMTP propio | Authentication > Emails > SMTP Settings con un proveedor propio (remitente de `<dominio>`, SPF y DKIM configurados). **El SMTP por defecto de Supabase solo entrega a miembros del equipo del proyecto** y con un tope muy bajo: sin SMTP propio, las invitaciones a staff y owners de tenants nunca llegan | **A mano**; las credenciales SMTP no se versionan |

Después de configurarlo: invitar a una cuenta de prueba que no sea del equipo de Supabase y comprobar que llega el correo,
que el enlace abre `/invitacion` en el dashboard y que se puede elegir contraseña y enrolar el MFA.

## Checklist del día del deploy

Lo que no se resuelve en código y hay que verificar con el sistema real antes de abrir al público. Cada punto se marca
con la evidencia (salida del `curl`, captura del panel), no de memoria. Los valores y la configuración de Auth están
en "Variables de entorno por servicio" y "Supabase Auth".

**Perímetro (Cloudflare, requiere dominio propio)**

- [ ] Los registros de `api.`, `admin-api.` y `bot.` están en modo proxy (nube naranja) y las respuestas traen el header
  `cf-ray`: `curl -sI https://api.<dominio>/health | grep -i cf-ray`.
- [ ] El acceso directo a `*.onrender.com` está bloqueado: Cloudflare agrega un header con un valor secreto (Transform
  Rules) y se rechaza lo que llega sin él. Verificar que `curl -si https://<servicio>.onrender.com/health` no devuelve
  200. Esto es lo que hace confiable a `LOGIN_IP_HEADER`: si se puede entrar sin pasar por Cloudflare, el header de IP
  se puede falsificar.
- [ ] `LOGIN_IP_HEADER=CF-Connecting-IP` cargada en dashboard-backend y admin-backend (sin ella el límite por IP cuenta a
  todos juntos). Probar: los intentos fallidos desde una IP no bloquean a otra.
- [ ] Reglas de rate limit del edge (login, `/auth/*`, webhook del bot) creadas, y verificado que superar el umbral
  devuelve 429 (un bucle de `curl` contra un endpoint de prueba) y que el tráfico legítimo sigue pasando.
- [ ] Turnstile con claves reales: `TURNSTILE_SECRET_KEY` en el backend y `VITE_TURNSTILE_SITE_KEY` en Vercel del frontend
  correspondiente, **las dos a la vez** y con el redeploy del frontend. Probar el login en cada frontend. Con solo la
  secret nadie puede iniciar sesión.
- [ ] Solo los puertos previstos abiertos: de cara a internet, 443 (y 80 solo para redirigir a HTTPS) a través de
  Cloudflare; el origen y la base no exponen otros puertos que no hagan falta (restricciones de red del proyecto de
  Supabase). Comprobar con un escaneo de puertos al dominio.

**Frontends (Vercel)**

- [ ] Headers verificados con curl sobre el dominio real: `curl -sI https://app.<dominio>/` y
  `curl -sI https://admin.<dominio>/` traen `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` y
  `Content-Security-Policy`.
- [ ] La CSP de `apps/*/vercel.json` tiene hoy `connect-src 'self' https:` (abierto porque el dominio no estaba
  definido). Con el dominio, acotar `connect-src` al backend de ese frontend (`https://api.<dominio>` o
  `https://admin-api.<dominio>`) y al host de ingesta de Sentry, redeploy y comprobar en la consola del navegador que
  el login y el envío de errores a Sentry siguen funcionando.

**Backends (Render)**

- [ ] `COOKIE_SECURE=true` y `COOKIE_SAMESITE=strict` en dashboard-backend y admin-backend (vienen fijas en el
  Blueprint: confirmar en el panel que no se pisaron) y que la cookie del refresh sale con `Secure` y `SameSite=Strict`.
- [ ] Auto-Deploy desactivado en los cinco servicios.
- [ ] Claves `sb_secret_` separadas por servicio: cada servicio que usa la Auth Admin API (dashboard-backend,
  admin-backend, job-purga) con su propia secret key de Supabase en `SUPABASE_SERVICE_ROLE_KEY`, para poder revocar una
  sin afectar a las otras; el bot y el job de vencimiento no llevan ninguna.
- [ ] Las contraseñas de los 6 roles de login son distintas, largas y están solo en el gestor de contraseñas y en el
  panel de Render.
- [ ] Los servicios instalan desde `requirements.lock` (ver "Lock de dependencias", donde también está cómo
  regenerarlo); `requirements-dev.lock` es solo del CI y del desarrollo local.

**Supabase**

- [ ] Configuración de Auth del proyecto hosteado cargada y verificada según "Supabase Auth" (Auth Hook, plantilla de
  invitación, Site URL y redirects, MFA, signup desactivado, contraseñas, reuso de refresh token).
- [ ] SMTP propio configurado y una invitación de prueba recibida por una cuenta ajena al equipo.

**Bot de Telegram**

- [ ] "Allow Groups" apagado en BotFather (`/mybots` > bot > Bot Settings > Allow Groups), para que no se pueda agregar el
  bot a grupos.
- [ ] El webhook apunta a `https://bot.<dominio>/api/bot/telegram/<webhook_key>` con `secret_token` (paso 10 del primer
  deploy) y `getWebhookInfo` no muestra errores.

**Monitoreo**

- [ ] Monitor de `GET /health` en cada backend (UptimeRobot, alerta por email) y probado que la alerta llega.
- [ ] Sentry recibe un error de prueba de cada proyecto y el monitor de cron de la purga queda creado.

## Primer deploy, paso a paso

Requisitos: dominio definido y su zona en Cloudflare; cuentas de Render, Vercel y Sentry listas; CLI de Supabase
con sesión (`supabase login`).

1. **Migraciones en producción.** `supabase link --project-ref tpbvxikgynwqvwsrjuyx` y `supabase db push` (pide la
   contraseña de la base; no se escribe en archivos). Aplica `supabase/migrations/` en orden. Antes, confirmar con
   `supabase db push --dry-run` qué va a aplicar.
2. **Cuenta del dueño y setup.** Crear en Supabase (Authentication > Users) la cuenta del primer administrador de la
   plataforma, y correr, con las contraseñas de los 6 roles generadas al azar en el gestor de contraseñas:
   `SETUP_DATABASE_URL=<...> python scripts/setup_proyecto.py --email <email> --pedir-passwords`. Inserta el primer
   `platform_admins`, siembra la clave `channel_user_hmac_key` en el Vault (si ya existe no la rota) y asigna las
   contraseñas de `bot_service`, `dashboard_service`, `platform_admin`, `platform_admin_readonly`, `purge_job` y
   `scheduler_job`. Con esas contraseñas se arman las cadenas de conexión del pooler.
3. **Sentry.** Tener a mano los 6 DSN (uno por proyecto de la organización `nesso-jx`).
4. **Render.** New > Blueprint, repo `turnos-saas`, rama `main`: crea los 3 web services y los 2 Cron Jobs. Render pide
   cada variable `sync: false`; se cargan con la tabla de arriba (las que dependen del dominio, con el dominio ya
   decidido). Confirmar que todos los servicios quedaron con Auto-Deploy desactivado. El primer build se dispara al
   crear el Blueprint; las siguientes veces, a mano.
5. **Dominios de los backends.** En cada servicio de Render, agregar su dominio personalizado (`api.`, `admin-api.`,
   `bot.`) y crear el CNAME en Cloudflare que Render indica (verificación y certificado).
6. **Vercel.** Un proyecto por frontend (Root Directory `apps/dashboard-frontend` y `apps/admin-frontend`; el resto
   viene de `vercel.json`). Cargar `VITE_API_URL`, `VITE_SENTRY_DSN` y `VITE_TURNSTILE_SITE_KEY`, asignar el dominio (`app.` / `admin.`) y hacer el
   primer deploy a producción (`vercel deploy --prod` o desde el panel: el deploy al push está desactivado).
7. **Cloudflare DNS.** Los CNAME de los frontends en "solo DNS" (sin proxy). Los de los backends, según se decida el
   perímetro.
8. **Google OAuth.** Registrar `GOOGLE_OAUTH_REDIRECT_URI` como URI de redirección autorizada en el cliente OAuth.
9. **Comprobar.** `GET https://api.<dominio>/health`, `https://admin-api.<dominio>/health` y
   `https://bot.<dominio>/health` responden 200. Iniciar sesión en cada frontend. Disparar a mano un Cron Job de
   cada tipo desde Render y ver que termina en verde.
10. **Webhook de Telegram** (una vez por tenant, después de cargar su integración desde el panel admin, que genera el
    `webhook_key` y guarda `bot_token` y `webhook_secret` en el Vault). Desde una terminal, sin dejar el token en el
    historial:
    `read -rs BOT_TOKEN` y `read -rs WEBHOOK_SECRET`, y luego
    `curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" --data-urlencode "url=https://bot.<dominio>/api/bot/telegram/<webhook_key>" --data-urlencode "secret_token=${WEBHOOK_SECRET}"`.
    Verificar con `getWebhookInfo`.
11. **Monitores.** UptimeRobot: un monitor HTTP por backend sobre `/health` (alerta por email). Sentry: el monitor de
    cron `purga-diaria-tenants` se crea solo en la primera corrida de la purga (el job lo declara en su check-in).
    Render: confirmar que las notificaciones de fallo de ejecución de los Cron Jobs llegan por email.

## Desplegar y revertir

- **Desplegar una versión:** merge a `main` (PR con CI en verde) y, recién después, deploy manual: Render > servicio >
  Manual Deploy > "Deploy latest commit" (o `render deploys create <service-id>`); Vercel: `vercel deploy --prod` o
  Redeploy desde el panel. Las migraciones van antes que el código que las necesita (`supabase db push`).
- **Revertir:** Render > servicio > Events/Deploys > "Rollback" al deploy anterior; Vercel > Deployments > "Promote to
  Production" sobre el anterior. Una migración ya aplicada no se revierte sola: se corrige con otra migración.

## Plan Free de Render

Los dos backends Free se duermen tras 15 minutos sin tráfico y el primer acceso tarda cerca de 1 minuto; las
instancias Free comparten 750 horas al mes por workspace. Un monitor de UptimeRobot (ping cada 5 minutos) los
mantiene despiertos, y dos servicios despiertos todo el mes (≈1.490 h) superan esas horas: Render suspendería los
servicios Free hasta el mes siguiente. Hasta decidirlo, conviene que UptimeRobot vigile solo el bot (Starter) y que
los dos backends Free se chequeen a mano, o pasarlos a Starter cuando arranque el piloto.

## Verificación de arranque

Qué se comprobó y cómo repetirlo. El script `scripts/verificar_arranque.py` lee `render.yaml`, completa las
variables `sync: false` con valores de prueba locales (contraseñas de los roles al azar, orígenes `localhost`),
levanta cada web service con su `startCommand` real (con un puerto libre en lugar de `$PORT`), consulta `GET /health`
(espera 200) y ejecuta cada Cron Job (`python -m jobs_purga`, `python -m jobs_vencimiento`), que contra una base sin
trabajo tienen que terminar con código 0. Necesita Supabase local levantado (`supabase start`) y las dependencias
instaladas.

- **En el CI** (job `Tests de integración Python (Supabase local)`, Python 3.12): el job instala el proyecto con
  `pip install -r requirements.lock` y `pip install --no-deps .` (sin `-e`, igual que el `buildCommand` de Render) en un virtualenv limpio, comprueba desde otro
  directorio que se puedan importar los 5 módulos de arranque y todos los `platform_*`, y corre el script con el
  intérprete de ese virtualenv. Se eligió el CI porque la máquina local tiene Python 3.10 (el proyecto pide 3.11+) y
  `pip install .` solo se puede probar con una versión soportada.
- **En local** (Python 3.10, sin instalar el proyecto): `python scripts/verificar_arranque.py --desde-fuentes` usa las
  mismas raíces de paquete que `pytest`. Requiere `uvicorn` importable.

Salida de la corrida local (2026-10-06, Python 3.10, Supabase local con las migraciones de `supabase/migrations/`,
`--desde-fuentes`):

```
OK   turnos-bot: uvicorn bot_app.main:app --host 0.0.0.0 --port $PORT -> GET /health = 200
OK   turnos-dashboard-backend: uvicorn dashboard_backend.main:crear_app_desde_entorno --factory --host 0.0.0.0 --port $PORT -> GET /health = 200
OK   turnos-admin-backend: uvicorn admin_backend.main:crear_app_desde_entorno --factory --host 0.0.0.0 --port $PORT -> GET /health = 200
OK   turnos-job-purga: python -m jobs_purga -> exit 0
OK   turnos-job-vencimiento: python -m jobs_vencimiento -> exit 0
```

La prueba con `pip install .` en un virtualenv limpio (Python 3.12) solo corre en el CI: ahí queda su salida.

## Lock de dependencias

Hay dos locks, ambos con `==` y hashes, para Python 3.12 en Linux (lo que usan Render, por `PYTHON_VERSION` en
`render.yaml`, y el CI):

- `requirements.lock`: solo runtime (`[project].dependencies` y sus transitivas). Es lo que instala Render
  (`pip install -r requirements.lock && pip install --no-deps .`); producción no lleva pytest, ruff ni otras
  herramientas de desarrollo. El paso "Instalar como en Render" del CI instala solo este archivo.
- `requirements-dev.lock`: runtime + dev (`[project.optional-dependencies].dev`). Se genera con `-c requirements.lock`,
  así que las versiones de runtime son idénticas a las de producción. Lo instalan los jobs de Python del CI y el
  desarrollo local (`pip install -r requirements-dev.lock && pip install --no-deps -e .`).

Regenerarlos (al agregar o cambiar una dependencia en `pyproject.toml`, o para actualizar versiones), desde la raíz y con
cualquier Python que tenga `uv` (no hace falta que sea 3.12: el destino se fija con las opciones). El orden importa:
primero el de runtime, después el de dev:

```
python -m pip install uv
uv pip compile pyproject.toml --python-version 3.12 --python-platform linux --generate-hashes --no-header -o requirements.lock
uv pip compile pyproject.toml --extra dev -c requirements.lock --python-version 3.12 --python-platform linux --generate-hashes --no-header -o requirements-dev.lock
```

Para actualizar una sola dependencia se agrega `--upgrade-package <nombre>` al primer comando (y se regenera el segundo).
Se commitean ambos archivos en el mismo PR; `tests/test_despliegue.py` falla si una dependencia directa de runtime no
queda fijada con hash, si una herramienta de desarrollo aparece en `requirements.lock`, o si las versiones de runtime
difieren entre los dos locks. Si cambia `PYTHON_VERSION` (Render) o la versión de Python del CI, se cambia
`--python-version` y se regeneran.
