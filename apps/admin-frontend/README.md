# admin-frontend

SPA (Vite + React + TypeScript, sin SSR) del panel de administración de la plataforma (ARCHITECTURE.md §4.1, §5.4).

## Origen

Esta app nació como una copia única de `platform/admin-frontend-template` el 2026-10-06, desde el commit `b6a86be`
de `main`. Desde acá diverge: los cambios se hacen en esta carpeta y no se vuelven a sincronizar con el template.
No comparte paquetes con el dashboard: los frontends son independientes. Se despliega en Vercel, en un proyecto
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
```

Requiere Node 20.19 o superior.

## Regla central: el navegador nunca habla con Supabase

Login, MFA y todos los datos van contra `admin-backend` (ARCHITECTURE.md §3.1.5), que hace de proxy de Supabase Auth.
Acá no hay SDK ni claves de Supabase. Lo hacen cumplir una regla de ESLint (`no-restricted-imports` sobre
`@supabase/*`) y `src/seguridad.test.ts` (dependencias, imports, `localStorage`/`sessionStorage` y variables `VITE_`).

| Variable | Uso |
|---|---|
| `VITE_API_URL` | URL del `admin-backend`. |
| `VITE_SENTRY_DSN` | DSN de Sentry (público por diseño). Vacío = Sentry apagado. |

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
`VITE_API_URL` y `VITE_SENTRY_DSN`.
