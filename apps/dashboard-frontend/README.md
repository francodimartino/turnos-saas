# dashboard-frontend

SPA de Vite + React + TypeScript (sin SSR) del panel de cada negocio. Se despliega en Vercel (ARCHITECTURE.md §5.3).
Esta primera versión trae el esqueleto, el login, la verificación y el alta de MFA TOTP, la aceptación de invitación,
las rutas protegidas y el marco con navegación filtrada por permisos. Las pantallas de negocio se suman después.

## Comandos

```
npm ci            # instala con el lockfile (versiones fijas)
npm run dev       # servidor de desarrollo en http://localhost:5173
npm run lint      # eslint
npm run typecheck # tsc --noEmit
npm run build     # typecheck + build de producción en dist/
npm test          # vitest (componentes con Testing Library)
npm run api:generar   # regenera src/api/generado/contrato.ts desde contratos/dashboard-backend.openapi.json
npm run api:verificar # falla si ese archivo no coincide con el contrato
```

Requiere Node 20.19 o superior.

## Tipos de la API

Los tipos de `src/api/*` salen del contrato OpenAPI del backend (`contratos/dashboard-backend.openapi.json`, que
regenera `python scripts/contrato_openapi.py`), no se escriben a mano. `openapi-typescript` (versión exacta en
`devDependencies`) genera `src/api/generado/contrato.ts`, que se versiona y no se edita. Al cambiar una ruta o un
modelo del backend: regenerar el contrato y después `npm run api:generar`; el test `src/api/contrato.test.ts` (y por
lo tanto el CI) falla si el archivo quedó desactualizado. En el código se usan como `Esquemas["ServicioOut"]`
(`src/api/tipos.ts`); cualquier campo renombrado en el contrato rompe `npm run typecheck`. Las respuestas sin cuerpo
(204) usan `solicitarSinContenido`.

## Turnos y cambios de horario

- `/turnos`: agenda por día o semana, solicitudes pendientes de aprobación y packs. Gestionar turnos (aprobar, rechazar,
  mover, cancelar, completar, crear) requiere `approve_bookings`; sin ese permiso la pantalla es de solo lectura.
- Cambios de horario pendientes (`approve_bookings`; la sección no existe sin ese permiso): lista los turnos que tienen un
  cambio por resolver (`GET /turnos?cambio_pendiente=true`, sin rango de fechas) con el horario actual, el propuesto y
  cuándo vence, en hora de Buenos Aires. La agenda y los pendientes muestran el mismo detalle en el turno afectado.
  "Aprobar cambio de horario" y "Rechazar cambio de horario" piden confirmación y llaman a
  `POST /turnos/{id}/cambio/aprobar` y `/rechazar`. Un cambio vencido (venció, el horario propuesto ya pasó o el turno
  original ya empezó: la misma regla del servidor, `cambioVencido` en `fechas.ts`) se distingue y solo admite rechazar.
- Mientras una acción está en vuelo, todos los botones de ese turno quedan deshabilitados (sin doble envío). Los 409
  `cambio_no_pendiente` y `cambio_vencido` vuelven a pedir las listas y dejan el mensaje arriba de la pantalla, porque la
  fila puede desaparecer; `horario_ocupado`, `operacion_concurrente`, 403, 404 y 422 se muestran en la fila.
- Tras un cambio confirmado se muestra, si corresponde, qué pasó con Google Calendar y con el aviso al cliente
  (`aviso` de la respuesta: `pendiente_de_envio`, `error`, `sin_integracion`, `cliente_anonimizado`, `sin_canal`); el texto
  sale de `avisoDeResultado` (`paginas/turnos/errores.ts`), para todas las acciones sobre turnos.

## Clientes, equipo y Google Calendar

- `/clientes` (`view_bookings`): búsqueda, alta manual (`manage_customers`), edición y anonimización (`delete_customers`,
  con confirmación escribiendo una palabra; es irreversible). Los datos adicionales salen de
  `end_customer_extra_fields` de los ajustes del local.
- `/equipo` (`manage_staff`): activar/desactivar; invitar, editar permisos y dar/quitar owner solo lo ve el owner.
  El catálogo de permisos con su descripción está en `src/paginas/staff/catalogoPermisos.ts`.
- Google Calendar por profesional, en `/profesionales`. La vuelta de Google puede llegar a
  `/profesionales?google_calendar=<resultado>&profesional=<id>` (redirect del backend) o a `/calendar/callback`
  (`GOOGLE_OAUTH_REDIRECT_URI` apuntando al dashboard), que canjea `code`/`state` con `POST /calendar/callback` y vuelve
  a `/profesionales`; así las cookies pueden ser `SameSite=Strict`.

## Apariencia y avisos al negocio

- `/apariencia` (`manage_schedule`, oculta sin ese permiso): logo, colores y plazo de cancelación, con vista previa.
  - Logo: `POST` y `DELETE /local/logo`. El archivo se valida antes de enviarlo (PNG, JPG o WebP por tipo MIME **y**
    extensión, hasta 1 MB = 1 048 576 bytes, sin SVG); el backend vuelve a validar por los bytes. El cuerpo viaja como
    binario (`solicitar(..., { binario })`), no multipart. El bucket de logos es privado y el backend no entrega una URL:
    la vista previa muestra la imagen elegida o la última subida en esa sesión (un `blob:` local, por eso la CSP de
    `vercel.json` permite `blob:` en `img-src`); tras recargar solo se avisa que hay un logo guardado.
  - Colores (`#RRGGBB`, vacío = sin color propio) y plazo de cancelación (entero de 0 a 720 horas, vacío = 24 horas):
    `PATCH /local/apariencia`, que solo recibe lo que cambió (`null` borra la clave). `logo_ruta` no se edita por
    `PATCH /local/settings` (422 `logo_no_editable`).
  - Los mensajes de error se arman por código (`mensajes.ts`), nunca con el texto del backend.
- `/avisos` (`manage_staff`, oculta sin ese permiso): quién del equipo recibe los avisos del negocio y por qué canal
  (`GET` y `PUT /local/avisos`). Los cambios de varias personas viajan en un solo pedido (así se puede pasar el aviso de
  una persona a otra); "Volver al default" borra la configuración propia de esa persona. WhatsApp se muestra
  deshabilitado con su aclaración: el backend lo rechaza (`canal_no_disponible`).

## Regla central: el navegador nunca habla con Supabase

Todo (login, MFA, invitación, datos) va contra `dashboard-backend` (ARCHITECTURE.md §3.1.5), que hace de proxy de
Supabase Auth. En este paquete no hay SDK de Supabase ni claves de Supabase. Lo hace cumplir:

- una regla de ESLint (`no-restricted-imports` sobre `@supabase/*`), y
- `src/seguridad.test.ts`, que falla si aparece una dependencia o un import de Supabase, un uso de
  `localStorage`/`sessionStorage` o una variable `VITE_` que no sea una de las permitidas.

Las únicas variables de entorno son públicas (Vite las incrusta en el bundle; nunca van secretos):

| Variable | Uso |
|---|---|
| `VITE_API_URL` | URL del `dashboard-backend`. |
| `VITE_SENTRY_DSN` | DSN de Sentry (público por diseño). Vacío = Sentry apagado. |
| `VITE_TURNSTILE_SITE_KEY` | Site key pública de Cloudflare Turnstile. Vacía = sin widget (login e invitación no mandan `turnstile_token`). |

## Dónde vive el token de sesión (decisión)

**En ningún lugar al que llegue el JavaScript.** El backend entrega la sesión en cookies `HttpOnly`, `Secure`,
`SameSite=Strict` (`dash_acceso` y `dash_refresco`) y nunca en el cuerpo de una respuesta. El frontend solo hace
`fetch(..., { credentials: "include" })` (`src/api/cliente.ts`). Consecuencias:

- No se guarda nada de la sesión en `localStorage` ni en `sessionStorage`: un XSS no puede robar el token.
- El estado de "quién soy" (`user_id`, `tenant_id`, `is_owner`, `aal`) se pide a `GET /auth/sesion` al cargar la app y
  se mantiene en memoria. Si el access token venció, se renueva con `POST /auth/refrescar` y se reintenta una vez.
- El paso de MFA pendiente tras el login (enrolar o verificar y el `factor_id`) también vive solo en memoria: si se
  recarga la página en medio del flujo, se vuelve a iniciar sesión.
- Frontend y backend tienen que ser del mismo sitio (por ejemplo `panel.ejemplo.com` y `api.ejemplo.com`), si no el
  navegador no envía la cookie `SameSite=Strict`. El backend también exige que `DASHBOARD_ORIGIN` sea exactamente el
  origen de esta app (CORS con credenciales y comprobación de `Origin` en los métodos que escriben).
- Un 401 sin posibilidad de renovar equivale a sesión cerrada: se vuelve a `/login`.

## Flujo de autenticación y MFA (ARCHITECTURE.md §3.6)

`POST /auth/login` y `POST /auth/invitacion/aceptar` devuelven `siguiente_paso`:

- `listo`: se consulta `/auth/sesion` y se entra al panel.
- `verificar_mfa` (con `factor_id`): pantalla `/mfa/verificar`, pide el código de 6 dígitos.
- `enrolar_mfa` (owner sin factor): pantalla `/mfa/enrolar`, muestra el QR y la clave, y pide el primer código. Es la
  única pantalla a la que llega un owner con sesión `aal1`.

`RutaProtegida` manda a `/login` sin sesión y manda a MFA a un owner con `aal` distinto de `aal2`. Es experiencia de
usuario: la barrera real es el backend, que rechaza esas sesiones.

El mail de invitación debe apuntar a `/invitacion?token_hash=...` de esta app.

## Permisos y navegación

`src/layout/secciones.ts` es el registro de secciones del panel; cada una declara el `permission_key` (§2.4) que la
habilita. El owner ve todo; el resto, solo lo que tiene asignado. Ocultar una sección no protege nada: cada endpoint
del backend vuelve a chequear el permiso.

Pendiente de backend: `GET /auth/sesion` hoy devuelve `user_id`, `tenant_id`, `is_owner` y `aal`, pero no la lista de
permisos. El cliente la lee de un campo opcional `permisos` (lista de `permission_key`); mientras el backend no lo
exponga, el personal no owner no ve ninguna sección con permiso requerido.

## Sentry sin datos personales

`src/observabilidad/sentry.ts`: `sendDefaultPii: false`, sin trazas y un `beforeSend` (equivalente al `before_send`
de los backends) que elimina usuario, request (cookies, headers, cuerpo, URL), breadcrumbs y datos extra, y redacta
emails y teléfonos en los mensajes de error. Los datos de Sentry se guardan en EE.UU. (§6.1).

## Despliegue

`vercel.json` reescribe todas las rutas a `index.html` (SPA) y agrega cabeceras de seguridad (CSP, `nosniff`,
`X-Frame-Options`, `Referrer-Policy`). En Vercel: framework Vite, directorio raíz `apps/dashboard-frontend`,
variables `VITE_API_URL`, `VITE_SENTRY_DSN` y, opcional, `VITE_TURNSTILE_SITE_KEY`. La CSP permite `https://challenges.cloudflare.com` solo en `script-src` y `frame-src`, y `blob:` en `img-src` (vista previa del logo antes de subirlo).
