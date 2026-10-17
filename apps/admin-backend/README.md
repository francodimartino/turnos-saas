# admin-backend

FastAPI (Render, deploy y credenciales separados del dashboard-backend; ARCHITECTURE.md §2.11, §5.3). Único punto por
el que el `admin-frontend` llega a los datos y a Supabase Auth: el navegador nunca habla con Supabase (§3.1.5).

Arranque: `uvicorn admin_backend.main:crear_app_desde_entorno --factory` (variables en `.env.example`; si falta
alguna o es insegura, el proceso no arranca).

## Piezas

- `config.py`: configuración por entorno. Pool como rol `platform_admin` (`ADMIN_DATABASE_URL`) y un segundo pool como `platform_admin_readonly`
  (`ADMIN_READONLY_DATABASE_URL`, obligatoria) solo para la impersonación. La `service_role` no
  está en `Config`: solo la lee `platform_admin.ClienteAuthAdminHttp` (Auth Admin API, invitaciones) en `main.py`.
- `apps/shared/supabase_proxy`: cliente de la REST de Supabase Auth (login, MFA TOTP, logout), **compartido** con el
  dashboard-backend (antes vivía en `dashboard_backend/supabase_auth.py`, que ahora solo lo reexporta). Va con la anon
  key; los errores se traducen a mensajes genéricos.
- `cookies.py`: cookies `adm_acceso`/`adm_refresco` (`HttpOnly`, `Secure`, `SameSite=Strict`) y middleware de
  origen/cookie, mismo mecanismo que el dashboard (ver su README). Los tokens nunca van en el body.
- `dependencias.py`: el **gate**. `deps.sesion` = `platform_auth.dependencia_admin`: JWT válido + claim
  `is_platform_admin` + `aal2` + confirmación en vivo contra `platform_admins` (403 si falla cualquiera; quitar a un
  admin corta su sesión ya). `deps.sesion_enrollment` acepta `aal1` solo para enrolar el segundo factor (§3.6). El
  claim solo habilita: el acceso a los datos lo da el rol Postgres.
- `rutas_auth.py`: `/auth/*`. `rutas_panel.py`: tenants, settings y facturación. `rutas_visores.py`: audit_log,
  notificaciones con problemas e integraciones. `rutas_impersonacion.py`: impersonación de solo lectura. `errores.py`: errores de dominio a
  404/409/422. `GET /health`: 200 solo si `select 1` responde (503 sin detalles, timeout 3 s).

## Endpoints

Todo el panel exige platform admin con `aal2`. `actor_id` del audit = `user_id` del JWT verificado.

| Método y ruta | Resultado |
|---|---|
| `POST /auth/login` | Cookies + `{"siguiente_paso": "verificar_mfa" (con `factor_id`) \| "enrolar_mfa" \| "listo"}`. 403 `sin_acceso_admin` si la cuenta no es admin (sin cookies). |
| `POST /auth/mfa/enrolar`, `POST /auth/mfa/verificar` | Enrollment TOTP (acepta `aal1` solo si no hay un factor verificado: si no, 403 `mfa_ya_configurada`) y sesión `aal2`. |
| `POST /auth/refrescar`, `POST /auth/logout`, `GET /auth/sesion` | Renovar, cerrar, quién soy. |
| `GET /tenants?status=`, `GET /tenants/{id}` | Lista y ficha: `id, name, slug, plan, status, deactivated_at, paid_until, created_at, invitacion_pendiente` más `owner_nombre` (string o `null`) y `owner_email` (string o `null`) del primer owner del tenant, leídos con `public.get_tenant_staff_identity` como `platform_admin`. Con cuenta vinculada son los de la cuenta; con invitación pendiente, `owner_email` y `owner_nombre` son los guardados con la invitación. |
| `POST /tenants` | Alta (`name`, `slug`, `settings?`, `owner_email`, `owner_nombre`) -> 201 `{tenant_id, estado_invitacion: ok \| invite_pendiente}` (no repite nombre ni email). `owner_nombre` es obligatorio (1 a 120 caracteres) y viaja a la Auth Admin API como user metadata `full_name`: si falta o es inválido, 422 `{detail: {codigo: "datos_invalidos", mensaje: "owner_nombre ..."}}` sin repetir el valor y sin crear nada. 409 si el slug existe. |
| `POST /tenants/{id}/suspender`, `levantar-suspension`, `dar-de-baja`, `reactivar` | 204. Transición inválida -> 409 `transicion_invalida`; no existe -> 404. |
| `POST /tenants/{id}/reenviar-invitacion` | Body opcional `{owner_email?, owner_nombre?}` (`owner_email` corrige el guardado). Si el invite falla, el nombre queda guardado con el email (`pending_invite_name`): el reenvío lo reusa sin que haya que volver a enviarlo, y un `owner_nombre` nuevo lo reemplaza. Nombre inválido -> 422 `datos_invalidos`. 409 si no hay invitación pendiente. |
| `GET/PUT /tenants/{id}/facturacion` | `paid_until` y `billing_notes`; solo se modifica lo que viene en el body (`null` borra). |
| `GET /settings`, `PUT /settings/{clave}` | `platform_settings`; body `{"valor": ...}`; clave desconocida o valor inválido -> 422. |

| `GET /tenants/{id}/audit-log?action=&actor_type=&desde=&hasta=&limit=&offset=` | Visor de `audit_log` del tenant (incluye las acciones de `platform_admin`, que el tenant no ve). 404 si no existe. |
| `GET /notificaciones/problemas?limit=` | `{notificaciones, limpiezas_calendar}`: notificaciones `failed` o `pending` hace más de 1 h (todos los tenants) y filas `calendar_scrub_failed`. |
| `PUT /tenants/{id}/integraciones/{tipo}` | Carga la credencial del tenant (solo `telegram_bot`): body `{config?, secret}` -> `{integration_id, tenant_id, integration_type, webhook_key}`. El `secret` nunca vuelve ni se loguea ni se repite en errores 422. Otro tipo -> 422. |
| `GET /tenants/{id}/impersonar/{bookings, bookings/{booking_id}, clientes, servicios, profesionales, packs}` | Impersonación **de solo lectura** (filtros y paginación como en el dashboard; `bookings` exige `desde` y `hasta`). |

Impersonación (§2.11.4): reusa `apps/shared/turnos_queries` con el `tenant_id` de la ruta de forma explícita y un
**pool propio** del rol `platform_admin_readonly` (`ADMIN_READONLY_DATABASE_URL`), dentro de `readonly_tx` (`BEGIN READ
ONLY`, con `app.current_tenant_id` fijado al tenant de la ruta): cualquier escritura la rechaza la base. Antes de leer, cada vista escribe una fila de `audit_log`
(`actor_type='platform_admin'`, `action='impersonate_view'`, metadata `{vista[, booking_id]}`) con el pool de
`platform_admin`; la policy restrictiva de `audit_log` la esconde al `dashboard_service`. Solo hay rutas GET.

Errores: `{"detail": {"codigo": ..., "mensaje": ...}}`. El email del owner nunca se devuelve ni se loguea.

## Protección del login

`/auth/login` aplica, en este orden: límite por IP, Turnstile y límite por email. `/auth/refrescar` y
`/auth/mfa/verificar` aplican el límite por IP (se cuenta antes de validar la sesión). Al superar un límite responde
429 `demasiados_intentos` con `Retry-After`. Con Turnstile activo, un `turnstile_token` ausente o inválido da 400
`turnstile_invalido` y un fallo de Cloudflare (red/5xx) da 503 `turnstile_no_disponible` (falla cerrado); en ambos casos
no se llama a Supabase Auth. Los contadores viven en memoria del proceso (cada instancia cuenta por separado). Nunca se
loguea email, IP ni token. Variables (validadas al arrancar; ver `admin_backend/config.py`):

| Variable | Defecto | Efecto |
|---|---|---|
| `LOGIN_LIMITE_IP` | 20 | intentos por IP en la ventana |
| `LOGIN_LIMITE_EMAIL` | 5 | intentos por email en la ventana (solo login) |
| `LOGIN_VENTANA_SEG` | 900 | ventana en segundos |
| `LOGIN_IP_HEADER` | vacío | `CF-Connecting-IP` o `X-Forwarded-For`; vacío = IP del socket (el header se ignora) |
| `LOGIN_PROXIES_CONFIABLES` | 1 | con `X-Forwarded-For`, proxies propios delante del servicio |
| `TURNSTILE_SECRET_KEY` | vacío | vacío = captcha apagado; con valor el login exige `turnstile_token` |

## Tests

`pytest apps/admin-backend` (sin instalar nada ni exportar `PYTHONPATH`). Supabase Auth y la Auth Admin API van
simuladas. `test_integracion.py` corre con `DATABASE_URL` contra Postgres real como `platform_admin` (se salta solo si
no está definida): un JWT de staff de tenant sin `is_platform_admin` recibe 403 en todos los endpoints, igual que un
admin con `aal1` o con el claim pero sin fila en `platform_admins`; más el ciclo de vida, el alta con invitación caída
y su reenvío, settings y facturación.
