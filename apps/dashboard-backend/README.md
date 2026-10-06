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
- `rutas_catalogo.py`, `rutas_disponibilidad.py`, `rutas_settings.py`: catálogo, disponibilidad y settings del local
  (ver "Endpoints de catálogo"). `esquemas_catalogo.py` (cuerpos), `errores_db.py` (errores de Postgres a 4xx) y
  `escritura_sql.py` (UPDATE parciales).
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

## Endpoints de catálogo, disponibilidad y settings

Lecturas (`GET`): cualquier staff activo del tenant. Escrituras: el permiso de la tabla (el owner vivo los tiene todos).
Lo de otro tenant o inexistente es siempre 404. Detalle de cada ruta en el docstring de su módulo.

| Recurso | Rutas | Escritura exige |
|---|---|---|
| Servicios | `/servicios`, `/servicios/{id}` (GET, POST, PATCH, DELETE) | `edit_prices` (todo lo que escribe) |
| Profesionales | `/profesionales`, `/profesionales/{id}` (GET, POST, PATCH, DELETE) | `manage_staff` |
| Servicios de un profesional | `/profesionales/{id}/servicios` (GET), `.../{service_id}` (PUT, DELETE) | `manage_staff` |
| Reglas semanales | `/reglas-disponibilidad`, `/{id}` (GET, POST, PATCH, DELETE) | `manage_schedule` |
| Bloqueos | `/bloqueos-disponibilidad`, `/{id}` (GET, POST, PATCH, DELETE) | `manage_schedule` |
| Settings del local | `/local/settings` (GET, PATCH) | `manage_schedule` |

- `confirmation_mode` acepta `auto` y `approval`; `deposit` devuelve 422 hasta que se integre el cobro.
- Desactivar un profesional (`PATCH {"is_active": false}`) elimina en la base el secret de su Google Calendar.
- Validación: la forma la revisa Pydantic; los límites de datos (duración > 0, precio >= 0, fin > inicio, día 0-6,
  bloqueo no vacío, `booking_approval_timeout_hours` 1-168) los hace cumplir la base y `errores_db.py` los traduce:
  `check` -> 422 `valor_invalido`, clave foránea al escribir -> 422 `referencia_invalida`, al borrar -> 409 `en_uso`
  (se desactiva en lugar de borrar), único -> 409 `duplicado`.
- `PATCH /local/settings` mezcla claves de primer nivel (`null` borra la clave) y solo hace `UPDATE tenants SET
  settings`; el resto de las columnas de `tenants` no es escribible por este rol.
- **Auditoría** (`platform_audit.registrar`, en la misma transacción del cambio: si el registro falla, el cambio se
  revierte; el actor es el staff de la sesión, sin datos personales ni nombres en la metadata):

  | Acción | Entidad | Metadata |
  |---|---|---|
  | `service_created` / `service_updated` / `service_deleted` | `services` | `campos` cambiados (solo nombres de columna) y `precio` `{anterior, nuevo}` cuando hay alta, baja o cambio de precio |
  | `professional_created` / `professional_updated` / `professional_deactivated` / `professional_deleted` | `professionals` | `campos` cambiados (`professional_deactivated` cuando el PATCH pone `is_active=false`) |
  | `settings_updated` | `tenants` | `claves` de `settings` enviadas (nunca los valores) |

  Reglas, bloqueos y la asignación de servicios a profesionales no se auditan.

## Staff y permisos (`rutas_staff.py`)

| Método y ruta | Exige | Resultado |
|---|---|---|
| `GET /staff` | `manage_staff` | Staff del tenant: `id`, `is_owner`, `is_active`, `es_yo`, `created_at`, `permission_keys`. Sin email ni `auth_user_id`. |
| `POST /staff/invitaciones` `{email, permission_keys}` | owner vivo | Invita por la Auth Admin API (`platform_auth.invitar_staff`); 201 si es nuevo, 200 si ya estaba invitado. |
| `PUT /staff/{id}/permisos` `{permission_keys}` | owner vivo | Reemplaza el set. 422 `permiso_desconocido` si alguna clave no está en el catálogo. |
| `PUT /staff/{id}/owner` `{is_owner}` | owner vivo | Da o quita `is_owner`. No se le da a alguien desactivado (409 `staff_inactivo`). |
| `POST /staff/{id}/desactivar`, `/reactivar` | `manage_staff` (owner vivo si el objetivo es owner) | `is_active` false/true; nunca se borra la fila. |

- `manage_staff` no alcanza para dar permisos ni `is_owner` (se podría auto-promover): esas rutas piden owner vivo, leído
  de la base en la misma transacción que la escritura. Un staff desactivado pierde el acceso en su próximo request.
- Último owner activo: lo protege el trigger de la base; el rechazo sale como 409 `ultimo_owner` con mensaje claro.
- Invitaciones: `crear_app(cliente_admin=...)` o, por defecto, `SupabaseAuthAdmin.desde_entorno()` con
  `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` del entorno (la clave no pasa por `Config`). Sin ellas, 503
  `invitaciones_no_configuradas`; fallo de la API: 503 (reintentable) o 502, sin detalles ni el email.
- Auditoría (`audit_log`, sin email): `staff_invited`, `staff_permissions_changed` (claves agregadas y quitadas),
  `staff_owner_granted`/`staff_owner_revoked`, `staff_deactivated`/`staff_reactivated`. Lo que no cambia nada no audita.

## Clientes finales y derecho al olvido (`rutas_clientes.py`)

| Método y ruta | Permiso (§2.4) | Resultado |
|---|---|---|
| `GET /clientes?busqueda=&limite=&offset=` | `view_bookings` | Lista del tenant; `busqueda` filtra por nombre o teléfono. |
| `GET /clientes/{id}` | `view_bookings` | Detalle con `extra_data`. 404 si no existe o es de otro tenant. |
| `POST /clientes` | `manage_customers` | Alta manual: `phone` (obligatorio; se guarda en E.164), `name`, `extra_data`. 409 `cliente_duplicado` (con el `cliente_id` existente) si el teléfono ya está cargado en el tenant (§2.8). |
| `PATCH /clientes/{id}` | `manage_customers` | Corrige `phone`/`name`/`extra_data`. 409 `cliente_anonimizado` si ya fue anonimizado. |
| `POST /clientes/{id}/anonimizar` | `delete_customers` | Llama a `anonymize_end_customer`, hace COMMIT y recién entonces limpia Google Calendar. |

No hay `DELETE`: `dashboard_service` no puede borrar `end_customers` (§3.2); borrar es anonimizar.

- **Teléfono:** `apps/shared/telefonos.normalizar_e164`, la misma función que usa el bot (§2.8), para que el alta
  manual y el alta por bot deduplicen. Acepta números sin `+`: sin `+` ni `00` se asume Argentina y todo número
  argentino se guarda como celular `+549` + área + abonado (admite el `0` de área, el `15` y la falta del `9`:
  `011 15-5550-0101`, `+54 11 5550-0101` y `5491155500101` dan el mismo valor); los de otro país necesitan `+` o
  `00`. Lo ambiguo da 422 `telefono_invalido`.
- **`sin_cliente_identificable`:** se deriva de `deleted_at` (los turnos `confirmed` de un cliente anonimizado se
  muestran sin cliente identificable; los `pending` futuros los cancela la base).
- **`extra_data`:** solo acepta las claves que el tenant declara en `tenants.settings.end_customer_extra_fields`
  (lista de claves o de `{"key": ..., "label": ...}`); sin configuración solo se acepta `{}`. Valores escalares.
- **Calendario:** segunda transacción, tras el COMMIT del borrado: `calendar_scrub_targets` + `limpiar_eventos`. Cada
  evento no limpiado queda en `audit_log` como `calendar_scrub_failed` con `{"event_id", "motivo"}` (sin PII). Si Google
  no está configurado (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`,
  `CALENDAR_OAUTH_STATE_SECRET`) el motivo es `configuracion_calendar` y el borrado no se rompe. Se emite además el log
  estructurado `anonymize_end_customer` (solo ids y conteos).

## Turnos y packs (`rutas_turnos.py`, `turnos.py`)

| Método y ruta | Permiso (§2.4) | Resultado |
|---|---|---|
| `GET /turnos?desde=&hasta=&estado=&professional_id=` | `view_bookings` | Turnos que se superponen con el rango (fechas con zona horaria), vía `turnos_queries`. |
| `GET /turnos/{id}` | `view_bookings` | Detalle. 404 si no existe o es de otro tenant. |
| `POST /turnos` | `approve_bookings` | Alta del negocio: nace `confirmed`, dura lo que el servicio; `booking_pack_id` opcional consume una sesión. |
| `POST /turnos/{id}/aprobar`, `/rechazar` | `approve_bookings` o el profesional vinculado (`professionals.tenant_user_id`) | `pending` a `confirmed` / `cancelled`. |
| `POST /turnos/{id}/cancelar` | `approve_bookings` | `pending` o `confirmed` a `cancelled` (nunca DELETE). |
| `POST /turnos/{id}/mover` `{start_at}` | `approve_bookings` | Solo `confirmed`: actualiza el `time_range` del mismo turno, misma duración. |
| `POST /turnos/{id}/completar` | `approve_bookings` | `confirmed` a `completed`. |
| `GET /packs?end_customer_id=`, `POST /packs` | `view_bookings` / `approve_bookings` | Lista y asignación (`origin='manual_grant'` siempre). |

- Estado inválido: 409 `estado_invalido` (la base lo fuerza igual con su trigger). Choque de horario (exclusion
  constraint): 409 `horario_ocupado`, traducido en `errores_db.py`. Pack sin sesiones, ajeno o vencido: 409
  `pack_no_disponible`; compra sin pagar: 422 por el trigger de la base.
- La sesión del pack la devuelve solo el trigger de la base al cancelar; acá no se toca `used_sessions` al cancelar.
- Dos etapas: transacción con bloqueo del turno + cambio + `audit_log` (COMMIT) y, después, Google Calendar (evento solo
  en `confirmed`: crea al aprobar/crear, actualiza al mover, borra al cancelar; el id se guarda en
  `google_calendar_event_id`) y el aviso al cliente por el bot (`notifications_log`, segundo plano con reintentos).
  Un fallo de Calendar no revierte nada ni da 500: queda `calendar_sync_failed` en `audit_log`. La respuesta trae
  `calendario` y `aviso` (`programado`, `cliente_anonimizado`, `sin_canal`, `canal_no_soportado`, `sin_integracion`).
- Un turno de un cliente anonimizado se devuelve con `sin_cliente_identificable: true` y no se le avisa.
- El alta y el movimiento del negocio no validan reglas de disponibilidad ni bloqueos (el negocio puede pisarlos); el
  único límite duro es el choque con otro turno del mismo profesional.

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
claves se generan en cada corrida); `test_integracion.py`, `test_staff_integracion.py` y `test_catalogo_integracion.py` (dos tenants, aislamiento por recurso) corren con `DATABASE_URL` contra Postgres real como
`dashboard_service` y se salta solo si no está definida.
