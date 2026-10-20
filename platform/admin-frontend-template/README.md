# admin-frontend

SPA (Vite + React + TypeScript, sin SSR) del panel de administración de la plataforma (ARCHITECTURE.md §4.1, §5.4).

## Fuente única y plantilla

Esta carpeta es la única fuente del frontend admin: los cambios se hacen acá. `platform/admin-frontend-template` es una
copia exacta (sin `node_modules` ni `dist`) que se regenera con `python scripts/sincronizar_admin_frontend.py`; un
test de pytest falla en el CI si difieren, así que no se edita a mano. Al aprovisionar un producto nuevo se copia la
plantilla y desde ahí evoluciona por separado. No comparte paquetes con el dashboard: los frontends son independientes. Se despliega en Vercel, en un proyecto
distinto del dashboard (ARCHITECTURE.md §5.3).

Trae el esqueleto, el login contra `admin-backend`, el enrollment y la verificación de MFA TOTP, las rutas protegidas y
el marco con cierre de sesión. Las pantallas de administración se registran en `src/layout/secciones.ts` y `App.tsx`.

## Pantallas

| Ruta | Qué hace |
|---|---|
| `/tenants`, `/tenants/nuevo` | Lista con filtro por estado (badges) y alta con el email del owner (`ok` / `invite_pendiente`). |
| `/tenants/:id` | Ficha: acciones permitidas según el estado (§2.2: `active`, `suspended`, `inactive`; `purged` es terminal y no ofrece ninguna), baja con confirmación fuerte (explica el período de gracia y la purga y pide escribir el slug), reenvío de la invitación pendiente (el email queda en el backend, no se vuelve a pedir), facturación manual (`paid_until`, `billing_notes`; vaciar un campo lo borra) y carga de la credencial de Telegram (solo escritura: el campo se vacía y el valor nunca se muestra). |
| `/tenants/:id/auditoria` | Visor de `audit_log` con filtros de acción, actor y rango de fechas. |
| `/tenants/:id/impersonar` | Impersonación de **solo lectura**: banner permanente "Modo solo lectura", sin controles de escritura ni llamadas que no sean GET. |
| `/notificaciones` | Notificaciones fallidas o atascadas y limpiezas de Google Calendar fallidas. |
| `/configuracion` | `platform_settings` (período de gracia de la baja). |

Todas las fechas se muestran en hora de Buenos Aires (`src/formato.ts`). Los componentes que usan 2 o más pantallas viven
en `src/compartido/`; el resto queda junto a su pantalla.

## Comandos

```
npm ci            # instala con el lockfile (versiones fijas)
npm run dev       # servidor de desarrollo en http://localhost:5173
npm run lint      # eslint
npm run typecheck # tsc --noEmit
npm run build     # typecheck + build de producción en dist/
npm test          # vitest (componentes con Testing Library)
npm run api:generar   # regenera src/api/generado/contrato.ts desde contratos/admin-backend.openapi.json
npm run api:verificar # falla si ese archivo no coincide con el contrato
```

Requiere Node 20.19 o superior.


## Tipos de la API

Los tipos de `src/api/*` salen del contrato OpenAPI del backend (`contratos/admin-backend.openapi.json`, que regenera
`python scripts/contrato_openapi.py`), no se escriben a mano. `openapi-typescript` (versión exacta en `devDependencies`)
genera `src/api/generado/contrato.ts`, que se versiona y no se edita. Al cambiar una ruta o un modelo del backend:
regenerar el contrato y después `npm run api:generar`; el test `src/api/contrato.test.ts` (y por lo tanto el CI) falla si
el archivo quedó desactualizado. En el código se usan como `Esquemas["TenantAdmin"]` (`src/api/tipos.ts`); un campo
renombrado en el contrato rompe `npm run typecheck`. Las respuestas sin cuerpo (204) usan `solicitarSinContenido`.

## Regla central: el navegador nunca habla con Supabase

Login, MFA y todos los datos van contra `admin-backend` (ARCHITECTURE.md §3.1.5), que hace de proxy de Supabase Auth.
Acá no hay SDK ni claves de Supabase. Lo hacen cumplir una regla de ESLint (`no-restricted-imports` sobre
`@supabase/*`) y `src/seguridad.test.ts` (dependencias, imports, `localStorage`/`sessionStorage` y variables `VITE_`).

| Variable | Uso |
|---|---|
| `VITE_API_URL` | URL del `admin-backend`. |
| `VITE_SENTRY_DSN` | DSN de Sentry (público por diseño). Vacío = Sentry apagado. |
| `VITE_TURNSTILE_SITE_KEY` | Site key pública de Cloudflare Turnstile. Vacía = login sin captcha (no se carga el widget ni se envía `turnstile_token`). Con valor, el login bloquea el envío hasta resolver el desafío; la CSP de `vercel.json` permite `challenges.cloudflare.com` solo en `script-src` y `frame-src`. |

## Sesión

El backend entrega la sesión en cookies `HttpOnly`, `SameSite=Strict` (`adm_acceso`, `adm_refresco`); el JavaScript solo
hace `fetch(..., { credentials: "include" })` (`src/api/cliente.ts`) y nunca ve un token. Frontend y backend deben ser
del mismo sitio, y el backend valida el `Origin` (CSRF). "Quién soy" sale de `GET /auth/sesion` (en memoria); si el
access token venció se renueva con `POST /auth/refrescar` y se reintenta una vez.

## Flujo de autenticación (ARCHITECTURE.md §3.6)

`POST /auth/login` devuelve `siguiente_paso`: `listo`, `verificar_mfa` (con `factor_id`) o `enrolar_mfa`. La MFA TOTP es
obligatoria para todo administrador: `RutaProtegida` solo deja ver el panel con `aal2`. Una cuenta que no es de la
plataforma recibe 403 `sin_acceso_admin`: en el login se muestra el mensaje y, si la sesión ya existía, la guardia
muestra "Sin acceso" en lugar del panel. La barrera real es el backend; esto es la experiencia de usuario.

## Sentry sin datos personales

`src/observabilidad/sentry.ts`: `sendDefaultPii: false`, sin trazas y un `beforeSend` que elimina usuario, request,
breadcrumbs y extras, y redacta emails y teléfonos (ARCHITECTURE.md §6.1).

## Despliegue

`vercel.json` reescribe las rutas a `index.html` y agrega cabeceras de seguridad (CSP, `nosniff`, `X-Frame-Options`,
`Referrer-Policy`). En Vercel: framework Vite, directorio raíz `apps/admin-frontend`, variables
`VITE_API_URL`, `VITE_SENTRY_DSN` y, si se usa captcha, `VITE_TURNSTILE_SITE_KEY`.
