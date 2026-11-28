# web-frontend

SPA de Vite + React + TypeScript (sin SSR) de la web pública de reservas (`agendemos.ar`, ARCHITECTURE.md §2.12). Está
prevista para Vercel (§5.3); todavía no se desplegó.

Hoy trae solo la base del proyecto y tres rutas:

- `/`: landing comercial para negocios (propuesta, cómo funciona y contacto).
- `/privacidad`: texto **provisorio** de la política de privacidad, con un aviso visible. La versión de la política es
  la constante `VERSION_POLITICA` de `src/politica.ts`; se cambia ahí cuando se reemplace el texto.
- `/<slug>`: la página de un negocio. Todavía no hay backend contra el que consultar, así que siempre muestra "esta
  página no está disponible". Un nombre reservado (`/precios`, `/contacto`...) o con formato inválido nunca se trata
  como negocio y muestra "no encontramos esta página".

No hay cliente de API ni llamadas de red a un backend propio: se agregan cuando exista el contrato del backend de la web.

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

## Reglas

- **Nombres reservados de slug**: `src/slugs/reservados.ts`. Es la misma lista que `slug_reservado()` en la base y
  `SLUGS_RESERVADOS` de `platform_admin`; `tests/test_listas_sincronizadas.py` las compara.
- **Sentry sin datos personales** (`src/observabilidad/sentry.ts`): `CAMPOS_PII` incluye los campos de `PII_CAMPOS` de
  Python más `codigo` y `token`; el mismo test lo compara.
- **Nada de datos de personas en el navegador**: ni `localStorage` ni `sessionStorage` (lo impide eslint y
  `src/seguridad.test.ts`). Cuando haya sesión, irá en una cookie `HttpOnly`.
- **Estructura**: `src/paginas/` (una por pantalla) y `src/compartido/` (lo usado por 2 o más páginas).
- **CSP** (`vercel.json`): solo el propio origen y el envío de errores a Sentry; sin `unsafe-inline` ni `unsafe-eval`.
  Cuando se sume el backend de la web habrá que agregar su origen a `connect-src`.

## Variables de entorno

Públicas (Vite las incrusta al compilar): `VITE_SENTRY_DSN` (vacío = Sentry apagado).
