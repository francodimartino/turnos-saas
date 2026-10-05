# dashboard-backend

FastAPI (Render). Único punto por el que el `dashboard-frontend` llega a los datos y a Supabase Auth
(ARCHITECTURE.md §3.1.5): el navegador nunca habla con Supabase.

Arranque: `uvicorn dashboard_backend.main:crear_app_desde_entorno --factory` (variables en `.env.example`; si falta
alguna o es insegura, el proceso no arranca).

## Piezas

- `config.py`: configuración por entorno. Solo la anon key de Supabase; la service_role no se lee acá.
- `supabase_auth.py`: cliente de la REST de Supabase Auth (GoTrue) para login, MFA TOTP, invitación y logout.
  Inyectable en `crear_app(cliente_auth=...)`; los errores de Supabase se traducen a mensajes genéricos.
- `cookies.py`: cookies de sesión y middleware de origen/cookie (ver abajo).
- `dependencias.py`: `DependenciasDashboard` combina `platform_auth` y `platform_tenancy`. Un endpoint pide
  `Transaccion` con `Depends(deps.tx)`, `deps.tx_con_permiso("<permission_key>")` o `deps.tx_solo_owner` y recibe una
  conexión dentro de una transacción de `dashboard_service` con `app.current_tenant_id` y `app.current_actor_id`
  ya seteados desde el JWT verificado y el estado vivo de la base. COMMIT al terminar, ROLLBACK ante cualquier
  excepción. `tenant_id` no se toma nunca de body, query ni headers. Los logs del endpoint llevan `tenant_id` y
  `actor_type` (`observabilidad`); Sentry arranca sin PII.
- `rutas_auth.py`: endpoints `/auth/*`. `GET /health` devuelve 200 solo si responde `select 1` en Postgres (503 sin
  detalles en otro caso, con timeout de 3 s).

## Endpoints de auth

| Método y ruta | Body | Resultado |
|---|---|---|
| `POST /auth/login` | `email`, `password` | Cookies de sesión + `{"siguiente_paso": "listo" \| "verificar_mfa" (con `factor_id`) \| "enrolar_mfa", "expira_en"}`. 403 `sin_acceso_dashboard` si la cuenta no tiene tenant (no se entregan cookies). |
| `POST /auth/mfa/enrolar` | — | `{factor_id, qr_code, secret, uri}`. Acepta sesión `aal1` de owner (excepción de §3.6). |
| `POST /auth/mfa/verificar` | `factor_id`, `codigo` (6 dígitos) | Reemplaza las cookies por la sesión `aal2`. |
| `POST /auth/invitacion/aceptar` | `token_hash`, `password` | Canjea la invitación, define la contraseña y abre sesión. |
| `POST /auth/refrescar` | — | Renueva las cookies con el refresh token de la cookie; si venció, 401 y borra las cookies. |
| `POST /auth/logout` | — | 204; cierra la sesión en Supabase (mejor esfuerzo) y borra las cookies. |
| `GET /auth/sesion` | — | `{user_id, tenant_id, is_owner, aal}`. |

Errores: `{"detail": {"codigo": ..., "mensaje": ...}}`, el mismo formato que usa `platform_auth`.

El mail de invitación de Supabase tiene que apuntar a una página del dashboard-frontend con
`?token_hash={{ .TokenHash }}` (plantilla "Invite user"); esa página manda `token_hash` y la contraseña elegida a
`/auth/invitacion/aceptar`.

## Clientes finales y derecho al olvido (`rutas_clientes.py`)

| Método y ruta | Permiso (§2.4) | Resultado |
|---|---|---|
| `GET /clientes?busqueda=&limite=&offset=` | `view_bookings` | Lista del tenant; `busqueda` filtra por nombre o teléfono. |
| `GET /clientes/{id}` | `view_bookings` | Detalle con `extra_data`. 404 si no existe o es de otro tenant. |
| `POST /clientes` | `approve_bookings` | Alta manual: `phone` (E.164 obligatorio), `name`, `extra_data`. 409 `cliente_duplicado` (con el `cliente_id` existente) si el teléfono ya está cargado en el tenant (§2.8). |
| `PATCH /clientes/{id}` | `approve_bookings` | Corrige `phone`/`name`/`extra_data`. 409 `cliente_anonimizado` si ya fue anonimizado. |
| `POST /clientes/{id}/anonimizar` | `manage_staff` | Llama a `anonymize_end_customer`, hace COMMIT y recién entonces limpia Google Calendar. |

No hay `DELETE`: `dashboard_service` no puede borrar `end_customers` (§3.2); borrar es anonimizar. El catálogo de
permisos no tiene claves propias de clientes, así que se reutilizan las más cercanas; si se quiere separarlas hace
falta una migración que agregue `manage_customers`/`delete_customers`.

- **Teléfono:** `apps/shared/telefonos.normalizar_e164` (compartida con el bot). Exige código de país explícito (`+` o
  `00`); no adivina el país de un número nacional.
- **`sin_cliente_identificable`:** se deriva de `deleted_at` (los turnos `confirmed` de un cliente anonimizado se
  muestran sin cliente identificable; los `pending` futuros los cancela la base).
- **`extra_data`:** solo acepta las claves que el tenant declara en `tenants.settings.end_customer_extra_fields`
  (lista de claves o de `{"key": ..., "label": ...}`); sin configuración solo se acepta `{}`. Valores escalares.
- **Calendario:** segunda transacción, tras el COMMIT del borrado: `calendar_scrub_targets` + `limpiar_eventos`. Cada
  evento no limpiado queda en `audit_log` como `calendar_scrub_failed` con `{"event_id", "motivo"}` (sin PII). Si Google
  no está configurado (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`,
  `CALENDAR_OAUTH_STATE_SECRET`) el motivo es `configuracion_calendar` y el borrado no se rompe. Se emite además el log
  estructurado `anonymize_end_customer` (solo ids y conteos).

## Cómo viajan los tokens hacia el navegador

- Solo en cookies `HttpOnly`, `Secure`, `SameSite=Strict` (configurable a `Lax`; `None` no se admite). Nunca en el
  body de una respuesta ni en `localStorage`: un XSS en el frontend no puede leerlos.
- `dash_acceso` (access token, `Path=/`, vida = `expires_in`) y `dash_refresco` (refresh token, `Path=/auth`, vida
  `COOKIE_REFRESH_MAX_AGE`, 7 días por defecto).
- Para que la cookie funcione, frontend y backend deben ser el mismo sitio (mismo dominio registrable, por ejemplo
  `panel.ejemplo.com` y `api.ejemplo.com`, algo que Cloudflare permite). Con dominios distintos `SameSite=Strict`
  haría que el navegador no la mande.
- El middleware convierte `dash_acceso` en `Authorization: Bearer ...` antes de llegar a `platform_auth`, que sigue
  siendo la única puerta de verificación. Un `Authorization` explícito gana sobre la cookie.
- CSRF: además de `SameSite`, todo método que escribe (POST/PUT/PATCH/DELETE) sin `Authorization` explícito debe
  traer `Origin` igual a `DASHBOARD_ORIGIN`; si no, 403 `origen_no_permitido`.
- CORS: un único origen (`DASHBOARD_ORIGIN`), con credenciales; sin comodines.
- Rate limiting y Turnstile del login se aplican en Cloudflare (§3.7); este servicio solo traduce el 429 de Supabase.

## Tests

`pytest apps/dashboard-backend` (sin instalar nada ni exportar `PYTHONPATH`). Supabase Auth va simulado (tokens y
claves se generan en cada corrida); `test_integracion.py` corre con `DATABASE_URL` contra Postgres real como
`dashboard_service` y se salta solo si no está definida.
