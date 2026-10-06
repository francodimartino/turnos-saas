# admin-backend

FastAPI (Render, deploy y credenciales separados del dashboard-backend; ARCHITECTURE.md §2.11, §5.3). Único punto por
el que el `admin-frontend` llega a los datos y a Supabase Auth: el navegador nunca habla con Supabase (§3.1.5).

Arranque: `uvicorn admin_backend.main:crear_app_desde_entorno --factory` (variables en `.env.example`; si falta
alguna o es insegura, el proceso no arranca).

## Piezas

- `config.py`: configuración por entorno. Pool como rol `platform_admin` (`ADMIN_DATABASE_URL`). La `service_role` no
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
- `rutas_auth.py`: `/auth/*`. `rutas_panel.py`: tenants, settings y facturación. `errores.py`: errores de dominio a
  404/409/422. `GET /health`: 200 solo si `select 1` responde (503 sin detalles, timeout 3 s).

## Endpoints

Todo el panel exige platform admin con `aal2`. `actor_id` del audit = `user_id` del JWT verificado.

| Método y ruta | Resultado |
|---|---|
| `POST /auth/login` | Cookies + `{"siguiente_paso": "verificar_mfa" (con `factor_id`) \| "enrolar_mfa" \| "listo"}`. 403 `sin_acceso_admin` si la cuenta no es admin (sin cookies). |
| `POST /auth/mfa/enrolar`, `POST /auth/mfa/verificar` | Enrollment TOTP (acepta `aal1`) y sesión `aal2`. |
| `POST /auth/refrescar`, `POST /auth/logout`, `GET /auth/sesion` | Renovar, cerrar, quién soy. |
| `GET /tenants?status=`, `GET /tenants/{id}` | Lista y ficha (`invitacion_pendiente`, sin email). |
| `POST /tenants` | Alta (`name`, `slug`, `settings?`, `owner_email`) -> 201 `{tenant_id, estado_invitacion: ok \| invite_pendiente}`. 409 si el slug existe. |
| `POST /tenants/{id}/suspender`, `levantar-suspension`, `dar-de-baja`, `reactivar` | 204. Transición inválida -> 409 `transicion_invalida`; no existe -> 404. |
| `POST /tenants/{id}/reenviar-invitacion` | Body opcional `{owner_email}` (corrige el guardado). 409 si no hay invitación pendiente. |
| `GET/PUT /tenants/{id}/facturacion` | `paid_until` y `billing_notes`; solo se modifica lo que viene en el body (`null` borra). |
| `GET /settings`, `PUT /settings/{clave}` | `platform_settings`; body `{"valor": ...}`; clave desconocida o valor inválido -> 422. |

Errores: `{"detail": {"codigo": ..., "mensaje": ...}}`. El email del owner nunca se devuelve ni se loguea.

## Tests

`pytest apps/admin-backend` (sin instalar nada ni exportar `PYTHONPATH`). Supabase Auth y la Auth Admin API van
simuladas. `test_integracion.py` corre con `DATABASE_URL` contra Postgres real como `platform_admin` (se salta solo si
no está definida): un JWT de staff de tenant sin `is_platform_admin` recibe 403 en todos los endpoints, igual que un
admin con `aal1` o con el claim pero sin fila en `platform_admins`; más el ciclo de vida, el alta con invitación caída
y su reenvío, settings y facturación.
