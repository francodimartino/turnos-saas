# admin-frontend-template

Template de la SPA (Vite + React + TypeScript, sin SSR) del panel de administración de la plataforma
(ARCHITECTURE.md §4.1, §5.4). Este directorio es la fuente; al aprovisionar el producto se copia una sola vez a
`apps/admin-frontend`, que a partir de ahí evoluciona por separado. No comparte paquetes con el dashboard: los
frontends son independientes. Se despliega en Vercel, en un proyecto distinto del dashboard (ARCHITECTURE.md §5.3).

Trae el esqueleto, el login contra `admin-backend`, el enrollment y la verificación de MFA TOTP, las rutas protegidas y
el marco con cierre de sesión. Las pantallas de administración se suman después en `src/layout/secciones.ts` y `App.tsx`.

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
`Referrer-Policy`). En Vercel: framework Vite, directorio raíz `apps/admin-frontend` (ya copiado), variables
`VITE_API_URL` y `VITE_SENTRY_DSN`.
