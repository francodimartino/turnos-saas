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
```

Requiere Node 20.19 o superior.

## Regla central: el navegador nunca habla con Supabase

Todo (login, MFA, invitación, datos) va contra `dashboard-backend` (ARCHITECTURE.md §3.1.5), que hace de proxy de
Supabase Auth. En este paquete no hay SDK de Supabase ni claves de Supabase. Lo hace cumplir:

- una regla de ESLint (`no-restricted-imports` sobre `@supabase/*`), y
- `src/seguridad.test.ts`, que falla si aparece una dependencia o un import de Supabase, un uso de
  `localStorage`/`sessionStorage` o una variable `VITE_` que no sea una de las dos permitidas.

Las únicas variables de entorno son públicas (Vite las incrusta en el bundle; nunca van secretos):

| Variable | Uso |
|---|---|
| `VITE_API_URL` | URL del `dashboard-backend`. |
| `VITE_SENTRY_DSN` | DSN de Sentry (público por diseño). Vacío = Sentry apagado. |

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
variables `VITE_API_URL` y `VITE_SENTRY_DSN`.
