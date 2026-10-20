# Arquitectura — turnos-saas (gestión de turnos para estéticas y peluquerías)

> Arquitectura del producto turnos-saas (gestión de turnos para estéticas y peluquerías). Su base de plataforma (`platform/`) está descripta en este mismo documento: §1.2 y §2–§4.
>
> Documento vivo, construido sección por sección. Cada decisión listada fue confirmada explícitamente con Franco antes de escribirse — nada acá es un default asumido sin preguntar.

---

## 1. Visión general y principios de diseño

### 1.1 Objetivo y escala de diseño
- Producto: turnos (estética/peluquería). Es el primero del orden de construcción (§1.2).
- **Escala objetivo**: diseñar cómodo para **10-50 tenants por producto** en el año 1. No se diseña para miles de tenants ni alto volumen de tráfico — sería sobre-ingeniería para esta etapa.
- **Recurso real**: un solo developer (Franco), **dedicación part-time** en paralelo a su trabajo en Quales/MeLi. El proceso tiene que ser sostenible sin equipo: automatizado donde el riesgo es alto (tests de aislamiento RLS), liviano donde no.

### 1.2 Orden de construcción
1. `platform/` mínimo: `tenancy` + `auth` + `secrets` + `audit` + **scaffold de admin genérico** (§2.11). `notifications`/`calendar` se construyen recién cuando un producto los necesita; `billing` queda stub indefinidamente hasta que la monetización (§1.3) esté decidida — ver detalle en §4.3. El admin entra en el mínimo por excepción al principio §1.7.6: no es especulación, ya está confirmado que todos los productos lo necesitan idéntico.
2. Primer producto end-to-end con un tenant piloto — valida el patrón completo en un caso real.
3. Hardening de `platform/` con lo aprendido en el piloto, y de esta especificación (§2–§4), para que los productos siguientes la construyan ya corregida.
4. Productos siguientes construyendo su propio `platform/` desde esta misma especificación (debería ser sensiblemente más rápido).

### 1.3 Monetización — abierto por diseño
Franco todavía no definió cómo va a cobrarle a sus tenants (el SaaS en sí, no lo que el tenant le cobra a su cliente final), y puede variar por producto. Decisión de diseño: `tenants.plan` y `tenants.status` quedan **desacoplados del mecanismo de cobro** — se pueden setear manualmente (cobro fuera del sistema, por transferencia/factura) o vía integración de pagos (`platform_billing`) más adelante, sin romper el esquema ni requerir migración.

### 1.4 Localización
- **Un solo timezone**: `America/Argentina/Buenos_Aires` para todos los tenants. Simplifica toda la lógica de horarios. Si aparece un tenant fuera de Argentina en el futuro, se resuelve puntualmente — no bloquea el diseño actual.
- **Zona horaria única en el código**: la define `apps/shared/turnos_negocio/zona.py` (`ZONA_HORARIA`, `ZoneInfo`) y la importan el bot, el dashboard y los jobs; ningún módulo fija un desfase fijo. Como la imagen de Render no trae base de zonas horarias, `tzdata` es dependencia de runtime (en `requirements.lock` y `requirements-dev.lock`, con hash).
- **Moneda**: ARS por default (implícito del mercado actual). El modelo no se rompe si hace falta ampliar a otra moneda después (columna `currency` se agrega sin migración destructiva).

### 1.5 Protección de datos personales
El diseño contempla **desde el modelo de datos** un mecanismo de borrado/derecho al olvido para `end_customers` (Ley 25.326). Principio: ningún dato personal es "permanente por defecto" — todo dato de un `end_customer` tiene un camino de borrado/anonimización. El mecanismo concreto (soft-delete, qué se anonimiza, quién lo puede disparar, período de retención) se define en la Sección 2, no acá — acá solo queda fijado el principio y que **no es opcional**.

### 1.6 Portabilidad / tolerancia a lock-in de Supabase
Se prioriza poder migrar de Supabase en el futuro por sobre la velocidad de desarrollo inmediata: **`platform/` abstrae Supabase detrás de interfaces propias** (`platform_auth`, `platform_tenancy`, `platform_secrets`, etc. no exponen el SDK de Supabase directamente a las apps de producto — las apps de producto solo conocen la interfaz de `platform/`). Más trabajo de diseño ahora, aceptado explícitamente a cambio de portabilidad futura. **Límite real de esta portabilidad, no cubierto por la abstracción de código**: el schema tiene FKs directas a `auth.users` (`tenant_users.auth_user_id`, `platform_admins.auth_user_id`, §2.10) — eso es la tabla interna de Supabase Auth, no algo portable a otro proveedor de Postgres sin más. Migrar de Supabase de verdad requeriría migrar identidades y rehacer esas FKs, no solo cambiar la implementación interna de `platform_auth`. La abstracción de código reduce el trabajo de portar la lógica de negocio; no elimina la migración de datos de identidad que haría falta igual.

### 1.7 Principios rectores (síntesis, para consultar en cualquier decisión futura)
1. **Aislamiento por defecto** — ningún dato de un tenant es visible a otro salvo que una policy lo permita explícitamente.
2. **Defensa en profundidad** — RLS es el límite real, nunca la única capa (roles mínimos, contexto de tenant explícito, WAF).
3. **Cero confianza implícita en el cliente** — `tenant_id`, claims e identidad nunca se toman del input del usuario sin verificar server-side.
4. **Seguridad y aislamiento no se negocian por velocidad**; todo lo demás (features, pulido visual) sí puede esperar.
5. **No sobre-diseñar** para una escala que no se va a tener el año 1, pero tampoco cerrar puertas caras de abrir después (portabilidad, borrado de datos personales).
6. **Extraer código compartido con evidencia, no con especulación** — `platform/` crece cuando un segundo producto real necesita algo que el primero ya resolvió, no antes. Excepción explícita: si ya está confirmado que todos los productos necesitan algo idéntico (ej. el panel admin, §2.11), construirlo una sola vez desde el inicio no es especular — es evitar rehacer tres veces algo ya decidido.
7. **Todo enum-como-texto se hace cumplir en la base, no solo en un comentario** — **toda columna enum-como-texto** (ej. `status`/`origin`/`payment_status`/`deposit_status`/`actor_type`/`channel`/`provider`/`payable_type`/`integration_type`/`confirmation_mode`/`plan`, §2.10, §7) lleva un `check (columna in (...))` real con los mismos valores documentados en su comentario, **y `not null`** (con default cuando aplique) — un `check` no rechaza un NULL (`NULL in (...)` es NULL, no false), y un NULL también escapa a un `exclude ... where (status <> ...)`. Documentar el rango válido sin hacerlo cumplir es la misma confianza implícita en la aplicación que este documento evita para todo lo demás (numéricos, rangos, `tenant_id`, §3.1.3).

---

## 2. Modelo de actores y tenancy

### 2.1 Modelo de actores (3 niveles B2B2C)
```
Franco (plataforma)
   └─ Tenant (mi cliente: la estética, la peluquería)
         ├─ tenant_users (dueño/staff del tenant, login con Supabase Auth)
         └─ end_customers (el cliente del tenant: el paciente, el comprador, el jugador)
               └─ interactúa vía bot (WhatsApp en producción, Telegram en dev/test), no tiene cuenta Supabase Auth
```
Dos niveles de confianza distintos → dos formas de autenticar: `tenant_users` vía Supabase Auth (JWT con claims custom `tenant_id`/`is_owner`, inyectado vía Auth Hook — ver §3.4; los `permission_key` de §2.4 no viajan en el JWT, se consultan en vivo); `end_customers` sin sesión Supabase, identificados por el par `(channel, channel_user_id)` — el ID que el propio canal verifica (`telegram_user_id` en Telegram, `wa_id` en WhatsApp) —, mediado siempre por el bot backend (nunca exponen `service_role`).

### 2.2 Ciclo de vida del tenant
- **Alta**: manual por Franco, desde el panel admin (§2.11) — no self-service todavía, dado el volumen esperado (10-50 por producto) y la dedicación part-time. La creación de un tenant es una **operación única de `admin-backend`** (`create_tenant(tenant_data, owner_email)`, en el paquete `platform_admin`), invocada por el admin panel hoy y, eventualmente, por un formulario de self-service sin cambiar la operación — sumar self-service después es "agregar un formulario que la llama", no un rediseño. `create_tenant` hace dos pasos orquestados desde Python, no una sola transacción SQL (enviar el invite es una llamada a la Auth Admin API, no algo que una función de Postgres pueda ejecutar): 1) una transacción DB que crea el `tenant` y su primer `tenant_user` (`is_owner=true`); 2) una llamada a la Auth Admin API de Supabase para invitar a `owner_email`. Si el paso 2 falla (ej. error de red), el tenant y el owner ya existen pero sin invite enviado — el admin panel expone una acción de "reenviar invitación" para ese caso, en vez de revertir la creación. Resuelve el punto de partida del flujo de invitaciones de §2.9 (alguien tiene que invitar al primer owner, ya que no hay un owner previo que lo haga).
- **Baja**: al cancelar, `tenants.status = 'inactive'` y `deactivated_at = now()`. Los datos quedan intactos durante un **período de gracia configurable**, contado desde `deactivated_at` (`platform_settings`, editable desde el panel admin §2.11; 90 días como valor inicial, sin motivo específico — Franco lo ajusta desde el panel). **Mínimo de 30 días, impuesto por la base** (un trigger de `platform_settings` rechaza un valor menor o que no sea un entero, y `list_tenants_due_for_purge` ignora un valor menor a 30 que ya estuviera guardado): nadie puede dejar la gracia en 1 día por error. Además, **bajar un valor vigente exige confirmación explícita** en el panel admin (`confirmar_reduccion`; sin ella el backend responde 409), porque acortar la gracia puede dejar vencidos de golpe tenants que todavía estaban a tiempo. Pasado el período, el Cron Job de purga (§5.4.1) corre una cascada que arranca con un paso previo: **`begin_tenant_purge(tenant_id)`** pasa el tenant de `inactive` a `purging` bajo `for update`, re-validando en la base que esté `inactive` con el período vencido (es idempotente sobre uno que ya está `purging`). Desde ahí **no se puede reactivar, suspender ni dar de baja** (ver estados): sin este paso, una reactivación entre `purge_tenant_data` y `mark_tenant_purged` dejaría un tenant activo, vacío e irrecuperable — la reactivación concurrente espera el lock y, al obtenerlo, el trigger de transiciones la rechaza. Si el job se interrumpe, la corrida siguiente **retoma** la purga: `list_tenants_due_for_purge` también lista los `purging`. Después: **0)** limpia los eventos de Google Calendar de todos los profesionales del tenant (`calendar_scrub_targets`, §3.5) **antes** de que se eliminen sus credenciales — los que no se puedan limpiar quedan como `calendar_scrub_failed`, igual que en el borrado individual; **1)** `purge_tenant_data(tenant_id)` (función `SECURITY DEFINER`, §3.2) — **la base re-valida** que el tenant esté `purging` (o `inactive` con el período vencido, que pasa antes por `begin_tenant_purge`), y si no, rechaza: un bug del job no puede purgar un tenant activo ni suspendido. Anonimiza todos sus `end_customers` (con la lógica de `anonymize_end_customer`, §3.5) y el `name` de sus `professionals` (dato personal del personal), borra las sesiones del bot que queden, elimina de Vault los secrets de todas sus `tenant_integrations` (las credenciales quedan inválidas) y devuelve los `auth_user_id` de sus `tenant_users`; **2)** el job elimina esas cuentas vía la Auth Admin API de Supabase (§2.9 — mecanismo distinto, no hay PII que anonimizar por SQL ahí; un 404 cuenta como éxito; **la cuenta de un `platform_admin` nunca se elimina**: antes de borrar, el job llama a `unlink_platform_admin_accounts(tenant_id)`, que desvincula esas filas de `tenant_users` y devuelve las cuentas para excluirlas, y el paso informa cuántas omitió como `omitidos`); **3)** `mark_tenant_purged(tenant_id)` (parte de `purging`) verifica que no quede ninguna cuenta vinculada y recién ahí setea `tenants.status='purged'` y **libera el slug** renombrándolo a `<slug>-purged-<fecha>` — si el negocio vuelve a contratar, se da de alta como una fila nueva con su slug original. Cada paso queda registrado en `audit_log` (`action='tenant_purge_step'`) y en los logs estructurados (§6.3), y es idempotente (reintentarlo sobre algo ya anonimizado/eliminado no rompe nada), así que si el Cron Job se interrumpe a mitad de la cascada, la corrida del día siguiente la retoma sin problema — pero sin el `status='purged'` final, no habría forma de saber cuándo parar de reintentar. Dar de baja el bot/número en sí en BotFather/Meta es un paso manual aparte, fuera del sistema.
- **Estados del tenant**:

  | `status` | Cómo se llega | Bot | Login dashboard | Reloj de purga |
  |---|---|---|---|---|
  | `active` | alta / reactivación | responde | sí | — |
  | `suspended` | Franco lo suspende (contención de incidente §6.5, u otra causa operativa) | no responde | bloqueado | **no corre** |
  | `inactive` | baja del tenant | no responde | bloqueado | corre desde `deactivated_at` |
  | `purging` | `begin_tenant_purge`, primer paso de la cascada (solo el Cron Job, con la gracia vencida) | no responde | bloqueado | purga en curso: no se puede reactivar; se retoma si se interrumpe |
  | `purged` | fin de la cascada de purga | — | — | terminado |

  Transiciones válidas: `active`↔`suspended`, `active`/`suspended`→`inactive`, `inactive`→`active` (reactivación dentro del período de gracia, vuelve `deactivated_at` a `null`), `inactive`→`purging` y `purging`→`purged` (solo el Cron Job; no existe `inactive`→`purged` directo). **`purging` solo avanza**: `purging`→`active`/`inactive`/`suspended` se rechaza en la base con `check_violation` (el panel lo muestra como "purga en curso", 409, no como error). `purged` es terminal. Se separa `suspended` de `inactive` porque contener un incidente no puede arrancar el reloj de purga: si el incidente se resolviera tarde, se anonimizaría un negocio que nunca pidió la baja. **Mecanismo de bloqueo**: no viaja en el JWT — `platform_auth` consulta `tenants.status` en vivo en cada request (igual que los `permission_key`), porque una suspensión tiene que cortar sesiones abiertas ya, no cuando expire el token. El bot ignora mensajes de un tenant no-`active`: la resolución de tenant del webhook (§2.6) solo devuelve tenants `active`.

### 2.3 Ciclo de vida del end_customer
- **Alta**: automática en el primer contacto por bot (crea el registro con `channel` + `channel_user_id` + datos mínimos), o carga manual desde el dashboard del tenant — necesario para migrar una base de clientes preexistente del negocio. En ambos caminos el teléfono se guarda normalizado a E.164 con la misma función (§2.8).
- **Multi-tenant membership**: la misma persona real puede ser cliente de varios tenants, pero **sin perfil compartido entre ellos** — cada tenant tiene su propia fila independiente (constraint único `(tenant_id, channel, channel_user_id)`), no hay tabla global de "personas". Consistente con el principio de aislamiento por defecto (§1.7): un tenant nunca ve nada de lo que esa persona hace en otro tenant.
- **Derecho de acceso (Ley 25.326, distinto del borrado)**: un comando al bot (ej. "mis datos") devuelve al end_customer sus propios `phone`/`name`/`extra_data` — no requiere schema nuevo, es una lectura de su propia fila ya accesible por `bot_service` dentro de su tenant. No estaba mencionado en ningún lado del documento pese a que la ley lo exige igual que el borrado.
- **Borrado / derecho al olvido (Ley 25.326)**: puede dispararlo el propio end_customer (comando al bot) o el tenant (desde el dashboard, con `delete_customers`) — los dos invocan la misma función de anonimización, sin período de gracia (a diferencia de la baja de tenant: acá ya hubo un pedido explícito). Franco (`platform_admin`) **no la invoca**: desde la migración `20260103001200` no tiene `EXECUTE` sobre ella; su camino es la purga de un tenant completo (§2.2), que anonimiza a todos sus clientes con la misma lógica. Se anonimizan `phone`, `name`, `extra_data`; **`bookings` pasados quedan intactos pero pierden el vínculo identificable** — el tenant conserva sus números de facturación/ocupación históricos (dato legítimo del negocio), sin retener datos personales del individuo.
- **Recontacto después de anonimizar**: al anonimizar, `channel_user_id` pasa a `null` y se guarda `channel_user_hash = HMAC-SHA256(clave, tenant_id ‖ channel ‖ channel_user_id)`, con una clave secreta por proyecto guardada en Vault. El hash se calcula **solo dentro de la base**, con funciones `SECURITY DEFINER` (§3.5): la clave nunca llega a la aplicación. Incluir `tenant_id` en el hash impide vincular a la misma persona entre tenants. Cuando llega un mensaje de un `channel_user_id` sin fila activa, el bot consulta si existe una fila dormida con ese hash. Si existe, **le pregunta a la persona**: "reactivar tu perfil anterior" (restaura `channel_user_id` en claro, borra el hash, vuelve a pedir teléfono/nombre y resetea `deleted_at` a `null`) o "empezar de cero" (crea una fila nueva; la vieja queda anonimizada y dormida para siempre). Si hay más de una fila dormida, se toma la más reciente. Reactivar sin preguntar sería restaurar datos que la persona pidió explícitamente borrar sin su consentimiento. **Por qué un hash y no borrar el ID**: mantener el flujo de recontacto (decisión de Franco) sin dejar guardado en claro el identificador, que en WhatsApp es el propio teléfono (`wa_id`). **Límite aceptado**: legalmente es seudonimización, no anonimización — con la clave se puede re-identificar. Por eso la clave no se rota salvo que se filtre; rotarla hace perder el recontacto de las filas viejas. **Barrera ante una segunda clave**: `pseudonymize_channel_user` devuelve `NULL` (no calcula ningún hash) si en Vault hay cero o más de una clave `channel_user_hmac_key`, con la comprobación en `hmac_clave_unica(text[])` (inmutable, sin `EXECUTE` para roles de aplicación); así una segunda clave cargada por error no produce hashes que no coincidan con los anteriores sin que nadie lo note. Rotar la clave es una operación manual e irreversible para el recontacto. **Edge case aceptado sin resolver por ahora**: si el tenant ya recargó manualmente a esta persona como fila nueva (sin saber que existía, porque el teléfono de la vieja quedó en `null`) antes de este recontacto, "empezar de cero" crearía una TERCERA fila en vez de fusionarse con la que el tenant ya cargó — no se resuelve automáticamente, es un caso raro que se acepta así por ahora.

### 2.4 Roles y permisos de tenant_users — RBAC granular
No alcanza con roles fijos (owner/admin/staff): se necesitan permisos finos desde el día 1.
```sql
permissions (
  key text primary key  -- catálogo completo en la tabla de §2.4
)

tenant_user_permissions (
  tenant_id fk,   -- desnormalizado desde tenant_users.tenant_id, forzado por trigger — mismo motivo que la nota de RLS de §7 (evita una policy con EXISTS contra tenant_users), aunque esta tabla viva en §2.10 y no en §7
  tenant_user_id uuid fk, permission_key text fk, granted_at timestamptz
)
```
Catálogo de `permissions` (sembrado por migraciones, idempotente) y qué habilita cada clave en `dashboard-backend`:

| Clave | Habilita |
|---|---|
| `view_bookings` | Ver turnos. |
| `approve_bookings` | Aprobar, rechazar, mover y cancelar turnos; también **crear turnos desde el dashboard y asignar packs** (`POST /turnos`, `POST /packs`, siempre `origin='manual_grant'`, §7.1). |
| `edit_prices` | Editar precios de servicios y packs. |
| `manage_staff` | Invitar y desactivar staff, y alta/edición de profesionales y de los servicios que hace cada uno. **No alcanza** para modificar `is_owner` ni los permisos de otro, ni para **vincular, re-vincular o desvincular la cuenta de un profesional** (`professionals.tenant_user_id`): eso lo hace solo el owner, porque decide con qué cuenta se aprueban los turnos de ese profesional (§2.9). |
| `manage_customers` | Alta y edición de clientes (`end_customers`). |
| `delete_customers` | Derecho al olvido desde el dashboard (`anonymize_end_customer`, §3.5). |
| `manage_schedule` | Horarios, bloqueos de disponibilidad y datos del local. |

El catálogo no tiene `view_reports`: no habilitaba nada (no hay reportes) y se retiró con sus asignaciones (migración `20260103001900`); si algún día hay reportes se vuelve a sembrar junto con la funcionalidad. Cada `tenant_user` tiene un set explícito de `permission_key`s, en vez de un rol fijo hardcodeado. El **owner** se modela con un flag `is_owner` que implica todos los permisos sin necesidad de asignarlos uno por uno. El dashboard puede ofrecer "templates" (ej. "Staff básico" = un set predefinido de permisos) como atajo de UX, pero el modelo de datos subyacente siempre es granular por permiso, no por rol.

### 2.5 Sucursales — diferido
Por ahora, **un tenant = un local físico**. Si aparece un cliente con varias sucursales, se resuelve en ese momento (ver `PENDIENTESPORDEFINICION.md`) — no se diseña `locations` de forma especulativa.

### 2.6 Resolución de tenant desde el bot
**El canal final de producción es WhatsApp, siempre — Telegram es exclusivamente para desarrollo/testing**, nunca un canal de producción alternativo. El mecanismo de resolución de tenant es el mismo patrón en ambos, por diseño:
- **Telegram (dev/test)**: un bot (token propio) por tenant, creado en BotFather — trivial y gratis, por eso es el canal de desarrollo.
- **WhatsApp (producción, definitivo)**: un **número de WhatsApp Business propio y verificado por tenant** — mismo patrón de "una credencial identifica al tenant sin ambigüedad", pero con el costo operativo real de la verificación de negocio de WhatsApp por cada alta de tenant (a diferencia de Telegram, que es instantáneo). Este costo operativo es aceptado como parte del proceso de alta de un tenant nuevo (§2.2).

Ambos casos se modelan igual en `tenant_integrations` (§4): una fila con `integration_type` distinto (`'telegram_bot'` en dev/test, `'whatsapp_business'` en producción) y su credencial en Vault — el código de resolución de tenant no cambia entre canales, solo cambia qué adapter de `platform_notifications` se instancia.

**Resolución del tenant en el webhook entrante** — es previa al contexto de tenant (RLS exige `app.current_tenant_id` antes de cualquier query, §3.1.3), así que se resuelve por un **identificador no secreto** guardado en `tenant_integrations.config` y una función `SECURITY DEFINER` con grants mínimos, nunca buscando una credencial por su valor (Vault no lo permite):
- **Telegram**: cada tenant tiene su propia ruta `/api/bot/telegram/{webhook_key}` — `webhook_key` es un UUID aleatorio **no secreto** (`config->>'webhook_key'`), registrado con `setWebhook`. El secret de Vault de esa integración es un JSON `{bot_token, webhook_secret}`; `webhook_secret` se registra como `secret_token` y Telegram lo manda en `X-Telegram-Bot-Api-Secret-Token`.
- **WhatsApp**: Meta entrega todos los números de una misma app a un único webhook; el tenant se resuelve por `metadata.phone_number_id` del payload (`config->>'phone_number_id'`). La firma `X-Hub-Signature-256` se verifica con el App Secret — **de plataforma** (uno por app, no por tenant, en Vault) — **antes** de resolver nada. **Provisorio**: el mecanismo exacto de firma depende del BSP (pendiente, `PENDIENTESPORDEFINICION.md`); lo que queda fijo es el patrón: verificar firma → resolver por identificador no secreto.
- **Orden de cada request**: 1) (WhatsApp) verificar firma de la app → 401 si falla. 2) `resolve_inbound_tenant(p_channel, p_external_key)` devuelve `(tenant_id, integration_id)` **solo si el tenant está `active`** (§2.2); si no devuelve nada → 200 y descarta, para que el canal no reintente. 3) `set local app.current_tenant_id`. 4) (Telegram) `get_integration_secret(integration_id)` — exige que la integración sea del tenant del contexto — y comparación del header en tiempo constante → 401 y rollback si no coincide. 5) Deduplicación: `insert into inbound_messages ... on conflict do nothing`; si no insertó, ya se procesó → 200. 6) Procesar **en la misma transacción** que la marca de deduplicación: si algo falla, el rollback borra la marca y la reentrega se reprocesa. Los mensajes de respuesta se envían después del commit.
- Un mismo POST de WhatsApp puede traer varias entradas (`entry[]`) de números distintos: se procesa **entrada por entrada**, resolviendo el tenant de cada una por separado, cada una en su propia transacción.
- Índices únicos globales (cross-tenant) sobre `config->>'webhook_key'` y `config->>'phone_number_id'`: dos tenants nunca pueden compartir un identificador.
- **Mensajes salientes desde otros componentes** (aviso de aprobado/rechazado desde `dashboard-backend`, aviso de vencimiento desde el Cron Job, §5.4.1): con el contexto de tenant seteado, obtienen la integración de bot del tenant con `find_integration(integration_type, professional_id)` (`SECURITY DEFINER`, devuelve solo el id de la integración del tenant del contexto) y su credencial con `get_integration_secret`, y registran el envío en `notifications_log`.

**Qué hace el bot en el piloto**: reservar turno (servicio → profesional que lo ofrece, §7.1 → horario → confirmar); ver mis próximos turnos; consultar servicios y precios; datos del local (dirección, horarios y contacto, en `tenants.settings`); hablar con el negocio (muestra el contacto del local — también es la salida para cancelar, §7.1); y los dos pedidos de la Ley 25.326: "mis datos" y "borrar mis datos" (§2.3). En Telegram el flujo es determinístico (menú/botones); si en WhatsApp es determinístico o agéntico sigue pendiente (`PENDIENTESPORDEFINICION.md`). Los recordatorios automáticos antes del turno quedan diferidos (pendientes, atados al BSP de WhatsApp: un mensaje iniciado por el negocio requiere plantillas aprobadas por Meta).

**Consultas y menú del bot** (`apps/bot/bot_app/consultas/`): el menú principal (Reservar turno, Mis turnos, Servicios y precios, Datos del local, Hablar con el negocio) es también la respuesta a todo mensaje que ningún flujo atiende, sin tocar la sesión. "Mis turnos" lista solo los turnos `pending`/`confirmed` futuros del propio cliente (filtra por `tenant_id` y `end_customer_id`), con horario en `America/Argentina/Buenos_Aires`; los pedidos de cancelar o reprogramar muestran el contacto del local (el bot no cancela ni reprograma en el piloto, §7.1). **Claves de `tenants.settings` que lee el bot** (todas opcionales, las carga el dueño desde el dashboard con `PATCH /local/settings`; si falta una, el bot lo dice sin romper): `local_direccion` (texto), `local_horarios` (texto, o lista de textos, uno por línea) y `local_contacto` (texto libre: teléfono, WhatsApp o email). Otros tipos de valor se ignoran y los textos largos se recortan a 300 caracteres. El botón de reservar usa `callback_data = 'reservar'`, que atiende el flujo de reserva; los de consultas usan el prefijo `con:`.

**Estado de la conversación**: en la tabla `bot_sessions` (§2.10) — paso actual y lo elegido hasta ahora, por `(tenant_id, channel, channel_user_id)` —, no en memoria del proceso: sobrevive a un redeploy de Render y queda aislada por tenant como el resto. Una sesión sin actividad por **1 hora** se descarta y el cliente arranca de nuevo (pasado ese tiempo, los huecos que vio pueden haber cambiado). **Modelo y versionado de la sesión**: en memoria es un `SesionBot` tipado (`apps/bot/bot_app/modelo_sesion.py`) con un campo por flujo (`reserva`, un `SesionReserva`, e `identidad`, un `SesionIdentidad` con el paso y, en el recontacto, el perfil anterior; las consultas no guardan estado) y ningún código indexa la sesión con claves de texto sueltas; en `bot_sessions.state` se guarda como JSON con un campo `version` en la sesión y otro en cada flujo. Al leerla, una sesión cuya versión no es la actual o cuya forma no es válida (claves desconocidas, tipos, ids que no son UUID, un paso sin lo elegido antes) se descarta como si no existiera: se borra la fila, se loguea solo el motivo (sin datos del cliente) y el cliente vuelve al menú. **Regla**: quien cambie la forma de un modelo sube su versión, y subirla descarta las sesiones en curso (duran una hora como máximo, así que el costo es que quien está a mitad de una conversación la empieza de nuevo). Una sesión guardada antes de que existieran las versiones (sin ningún `version`) se migra si su forma es válida.

### 2.7 tenant_users — un solo tenant por persona
Cada `tenant_user` pertenece a un único tenant (`auth_user_id` único en la tabla, no compuesto con `tenant_id`). Si la misma persona real gestiona dos negocios, necesita dos cuentas (dos emails) distintas — se prioriza simplicidad sobre soporte a staff multi-tenant.

### 2.8 Deduplicación de end_customers
En el primer contacto de un `channel_user_id` nuevo, se busca entre los `end_customers` del tenant (cargados manualmente antes, §2.3) uno con el mismo `phone`, normalizado a E.164 al guardarse (tanto en alta manual como por bot). En WhatsApp el teléfono viene del propio canal (`wa_id`); en Telegram, el bot lo pide con el botón de compartir contacto. Si matchea, vincula `channel`/`channel_user_id` al registro existente en vez de crear uno nuevo — evita duplicados entre alta manual y alta por bot.

**Normalización a E.164 y vínculo** (una sola función, `apps/shared/telefonos`, usada por el bot y por el alta manual del dashboard, para que ambos guarden el mismo valor y la deduplicación funcione): sin `+` ni prefijo `00` se asume Argentina; todo número argentino queda como celular `+549` + área + abonado (10 dígitos; WhatsApp y Telegram identifican a las personas por su número de celular, y un fijo o un celular sin el `9` darían un valor distinto para la misma persona), aceptando el `0` de troncal, el `15` entre área y abonado y la ausencia del `9`; los números de otro país con `+`/`00` se conservan (8 a 15 dígitos); lo que no cierra se rechaza (nunca se adivina). Solo se aceptan dígitos ASCII (`0-9`): los dígitos y separadores Unicode (árabes, de ancho completo, etc.) se rechazan en lugar de normalizarse. El alta manual también acepta números sin `+`. El bot solo acepta el teléfono por el botón de compartir contacto y solo si el contacto es del propio remitente (un número tipeado no prueba nada, y vincularlo daría el perfil de otra persona); se vincula únicamente a filas vivas del mismo tenant que todavía no tienen canal.

### 2.9 Alta de staff e invitaciones
- El owner invita nuevos `tenant_users` por email desde el dashboard, vía el flujo de invitación estándar de Supabase Auth, asignándole el set de permisos correspondiente (§2.4).
- **Cómo viaja la invitación**: `dashboard-backend` (staff) y `admin-backend` (owner de un tenant nuevo o reenvío, §2.2) llaman a la Auth Admin API de Supabase; quien es invitado acepta desde el dashboard con `POST /auth/invitacion/aceptar` (token + contraseña), que es el proxy de Auth de §3.1.5 — el navegador nunca habla con Supabase, ni siquiera para aceptar la invitación. Los owners sin MFA salen de ese flujo hacia el enrolamiento de §3.6.
- **Nombre y email del staff y de los owners siempre visibles** (decisión de producto del 2026-10-06): el dashboard muestra a cada `tenant_user` con nombre y email, y el panel admin muestra los owners de cada tenant. `tenant_users` no guarda esos datos (viven en `auth.users`: `email` y `raw_user_meta_data->>'full_name'`, el nombre en la metadata de Supabase Auth), y ni `dashboard_service` ni `platform_admin` pueden leer `auth.users`: la lectura pasa por la función `SECURITY DEFINER` `get_tenant_staff_identity(tenant_id)` (migración `20260103000700_identidad_staff.sql`), que devuelve `(tenant_user_id, email, nombre)` de todo el staff del tenant. Con la invitación pendiente (cuenta sin vincular) el email sale de `pending_invite_email` y el nombre de `pending_invite_name` (ambos los guarda `platform_admin` cuando el invite falla, y los anula la base al vincular la cuenta y la purga); si la cuenta no tiene `full_name` cargado, el nombre también es `null`. Autoriza por `session_user` (no esquivable con `SET ROLE`): `dashboard_service` solo si `app.current_tenant_id` coincide con el tenant pedido, `platform_admin` cualquiera, el resto falla; `EXECUTE` solo para esos dos roles. **Por qué una función y no un grant**: abrir `auth.users` a un rol de aplicación expondría los emails de toda la plataforma; la función entrega solo los del tenant del caller.
- **Invariante**: no se puede eliminar, desactivar ni quitarle el flag `is_owner` al último owner activo de un tenant — siempre debe quedar al menos uno con acceso total. La hace cumplir un **trigger** en `tenant_users` (no solo la aplicación, §1.7.2), con su test pgTAP negativo.
- **Escalación de privilegios**: dentro del flujo normal de `dashboard-backend`, solo un `tenant_user` con `is_owner=true` puede setear/quitar `is_owner` a otro, o modificar el set de `permission_key`s de otro `tenant_user`. El permiso genérico `manage_staff` (§2.4) **no alcanza** para esto — si alcanzara, un empleado con `manage_staff` podría auto-promoverse a owner o a cualquier `permission_key` que quisiera. `platform_auth` chequea esto como un caso separado del chequeo genérico de `permission_key`, no como un permiso más de la lista, leyendo `tenant_users.is_owner`/`is_active` **en vivo** (no el claim del JWT, §3.4) — un owner degradado pierde el poder en el acto, no cuando expira su token. **Lo mismo vale para el vínculo profesional-cuenta** (`professionals.tenant_user_id`, §7.1): vincular, re-vincular o desvincular una cuenta lo hace solo el owner vivo (leído en la base, en la misma transacción del cambio; 403 si no), porque quien tiene esa cuenta aprueba los turnos como ese profesional — con `manage_staff` solo se podría ceder esa capacidad a una cuenta propia. El `audit_log` registra los ids de `tenant_users` anterior y nuevo (`vinculo: {anterior, nuevo}`). **Esto no aplica a `create_tenant`** (§2.2): ese flujo corre en `admin-backend` como `platform_admin`, un código completamente distinto que nunca pasa por este chequeo de `platform_auth` — no es una excepción a la regla, es que la regla vive en un code path que `create_tenant` ni siquiera atraviesa.
- **Baja de staff**: se desactiva (`is_active=false`, revoca login/permisos), nunca se borra el registro — `audit_log` necesita conservar la trazabilidad de qué hizo esa persona mientras trabajó ahí. El derecho al olvido de §2.3/§1.5 aplica por diseño solo a `end_customers`; para `tenant_users` (personal del negocio) no es prioridad por ahora — **mientras el tenant siga activo**. Esto es distinto del caso de §2.2 (purga completa del tenant tras el período de gracia): ahí el negocio entero deja de existir, así que el staff también se anonimiza en esa cascada — no es una excepción a esta regla, es un escenario distinto (persona que se va de un negocio que sigue operando, vs. negocio que deja de existir del todo). **Importante**: `tenant_users` no tiene columnas de PII propias salvo una, transitoria: `pending_invite_email` (email del owner invitado, guardado solo mientras la invitación está pendiente para poder reenviarla sin volver a pedirlo; la base lo anula al vincular la cuenta y `purge_tenant_data` lo anula en la purga; solo `platform_admin` lo lee y escribe, grants por columna). El resto (nombre/email) vive en `auth.users`, la tabla interna de Supabase Auth — no existe un `anonymize_end_customer`-style para esta tabla porque no hay nada que anonimizar vía SQL. "Anonimizar" un `tenant_user` en la purga de §2.2 significa que el Cron Job (§5.4.1, Python) llama a la **Auth Admin API de Supabase para eliminar la cuenta** de cada `tenant_user` del tenant purgado (misma API que usa `create_tenant` para el invite), no un `UPDATE` SQL.

### 2.10 Esquema de datos consolidado (todo lo anterior, en tablas)

*Los `status`/`actor_type`/`payable_type`/etc. de las tablas de abajo también llevan su `check (columna in (...))` per §1.7.7 — no se repite en cada tabla.*

```sql
tenants (
  id uuid pk, name, slug unique,
  plan text not null default 'pilot',    -- check (plan in ('pilot')) hasta decidir la monetización (§1.3); sumar valores es una migración chica, no destructiva
  status text not null default 'active',  -- 'active' | 'suspended' | 'inactive' | 'purging' | 'purged' (§2.2) — 'purging' = purga en curso (no se puede reactivar); 'purged' se setea al terminar la cascada de §2.2, para que el Cron Job (§5.4.1) sepa no reprocesar un tenant ya purgado del todo en cada corrida diaria futura
  deactivated_at timestamptz null,        -- se setea al pasar a 'inactive'; desde acá corre el período de gracia (§2.2)
  -- check (status <> 'inactive' or deactivated_at is not null): un inactive sin fecha nunca se purgaría; ídem para 'purging' (sin fecha de baja no hay de dónde contar la gracia)
  -- trigger de validación de settings: 'booking_approval_timeout_hours' (entero 1-168, §7.1) y los límites de reserva del bot, si están: 'booking_max_active_per_customer' (entero 1-50, default 5), 'booking_max_per_hour' (entero 1-60, default 3), 'booking_min_notice_minutes' (entero 0-1440, default 30); el default lo aplica el bot, no se guarda
  -- validación en el dashboard-backend y en el alta de tenant del admin-backend, antes de llegar a la base, con las mismas reglas (`turnos_dominio.settings_tenant`; el admin las aplica en la ruta, porque `platform_admin` no importa `apps/shared`): cada clave conocida se valida por tipo, largo y rango con las mismas reglas que el trigger; el conjunto de `settings` no puede superar 20 KB acumulados (422 `settings_demasiado_grandes` en el PATCH del dashboard; en el alta del admin, el mismo tope responde 422 de validación); valores no finitos (NaN, infinito) responden 422, no 500. Un valor fuera de rango en una clave `booking_*` responde la lista de errores de validación de FastAPI (`detail` como lista), no `detail.mensaje`.
  settings jsonb default '{}',                  -- la define el propio tenant (§2.11): campos accesorios, datos del local para el bot (§2.6), 'booking_approval_timeout_hours' (default 24, §7.1)
  paid_until date, billing_notes text,          -- tracking manual de pago, sin integración de cobro todavía (§1.3)
  created_at
)

tenant_integrations (
  id uuid pk, tenant_id fk,
  integration_type text not null,   -- 'telegram_bot' (dev/test) | 'whatsapp_business' (producción) | 'google_calendar' | 'mercadopago'
  professional_id uuid null,  -- seteado solo para integraciones por-profesional (ej. google_calendar); null para integraciones de tenant (bot, WhatsApp)
  secret_id uuid unique,   -- referencia a Supabase Vault, nunca la credencial en claro (§4) — unique para que un bug no pueda hacer que dos filas (de tenants distintos, incluso) compartan el mismo secret sin que nadie lo note
  config jsonb default '{}',   -- datos NO secretos: 'webhook_key' (telegram_bot), 'phone_number_id' (whatsapp_business), §2.6
  created_at
)
-- índices únicos globales sobre (config->>'webhook_key') y (config->>'phone_number_id'), §2.6
-- dos índices únicos parciales, porque un unique(...) normal no protege
-- filas con professional_id NULL entre sí (NULL nunca es igual a NULL):
-- create unique index on tenant_integrations (tenant_id, integration_type) where professional_id is null;
-- create unique index on tenant_integrations (tenant_id, integration_type, professional_id) where professional_id is not null;

tenant_users (
  id uuid pk, tenant_id fk,
  auth_user_id uuid null unique references auth.users on delete set null,  -- nullable: la purga (§2.2) elimina la cuenta y la fila sobrevive para la trazabilidad de audit_log
  is_owner boolean default false, is_active boolean default true,
  created_at
)

permissions (
  key text primary key   -- catálogo completo en la tabla de §2.4
)

tenant_user_permissions (
  tenant_id fk,   -- desnormalizado desde tenant_users.tenant_id, forzado por trigger — mismo motivo que la nota de RLS de §7 (evita una policy con EXISTS contra tenant_users), aunque esta tabla viva en §2.10 y no en §7
  tenant_user_id uuid fk, permission_key text fk, granted_at timestamptz
)

end_customers (
  id uuid pk, tenant_id fk,
  channel text null,                  -- 'telegram' | 'whatsapp'; null en altas manuales (§2.3) todavía no vinculadas
  channel_user_id text null,          -- telegram_user_id / wa_id; null en altas manuales o tras anonimizar — el unique de abajo no choca entre varias filas null (NULL nunca es igual a NULL), que es justamente el comportamiento correcto acá
  channel_user_hash text null,        -- solo en filas anonimizadas (§2.3); nunca lo calcula la aplicación
  phone, name,                        -- phone en E.164 (§2.8); obligatorios AL CREAR (validado por bot_service/dashboard_service al insertar) — NUNCA `not null` a nivel de columna, porque anonymize_end_customer los pone en null después y esa función tiene que poder ejecutar sin violar un constraint
  extra_data jsonb default '{}',      -- accesorios, configurable por tenant
  deleted_at timestamptz,             -- marca de anonimización (derecho al olvido)
  created_at,
  unique (tenant_id, channel, channel_user_id),
  check ((channel is null) = (channel_user_id is null and channel_user_hash is null))  -- hay canal si y solo si hay un identificador (en claro o en hash)
)
-- índices: (tenant_id, phone) para la deduplicación de §2.8; (tenant_id, channel, channel_user_hash) para el recontacto de §2.3

platform_settings (
  key text primary key, value jsonb   -- ej. 'tenant_deletion_grace_period_days': 90
)
-- trigger: 'tenant_deletion_grace_period_days' debe ser un entero >= 30 (§2.2)
-- la migración inicial (§4.2) SIEMBRA 'tenant_deletion_grace_period_days': 90 como
-- fila real, no lo deja "sin configurar" — si el Cron Job (§5.4.1) corriera antes
-- de que Franco lo ajuste manualmente y la tabla estuviera vacía, "sin configurar"
-- podría interpretarse como 0 días y purgar todo lo inactivo de inmediato

platform_admins (
  auth_user_id uuid primary key references auth.users on delete cascade,  -- Franco, una fila por proyecto Supabase (por producto)
  added_at timestamptz default now()
)
-- bootstrap: la primera fila (Franco) se inserta con service_role vía un script
-- de setup inicial del proyecto (que también siembra en Vault la clave HMAC de §2.3 y,
-- en producción, el App Secret de WhatsApp de §2.6 — sin la clave, pseudonymize_channel_user
-- devuelve NULL y la anonimización falla cerrada por el check de end_customers), no desde ningún panel — no hay quién más lo dé de alta
-- la primera vez. Agregar un segundo platform_admin más adelante sí puede hacerse
-- desde el panel admin, por un platform_admin ya existente.
-- invariante (mismo patrón que el último owner de un tenant, §2.9): no se puede
-- eliminar la última fila de platform_admins — evita quedarse sin ningún acceso
-- de plataforma por accidente. La hace cumplir un trigger BEFORE DELETE (también
-- frena el cascade si se intentara borrar la cuenta Auth del último admin), con test pgTAP.

audit_log (
  id uuid pk, tenant_id fk null,  -- nullable: acciones de platform_admin sin tenant único (ej. editar platform_settings)
  actor_type text not null,  -- 'staff' | 'bot' | 'system' | 'platform_admin'
  actor_id, action, entity, entity_id, metadata jsonb, created_at,
  chain_seq bigint not null, prev_hash bytea null, hash bytea not null   -- cadena de hashes por tenant, abajo
)
-- solo-agregar: ningún rol tiene UPDATE/DELETE; se escribe únicamente vía
-- write_audit_log() (SECURITY DEFINER, deriva actor_type de session_user como §3.5)
-- cadena de hashes (migración 20260103001800): un trigger BEFORE INSERT calcula chain_seq (posición en la
-- cadena), prev_hash (hash de la fila anterior) y hash = sha256(prev_hash || contenido canónico de la fila),
-- ignorando lo que mande quien inserta; hay una cadena por tenant y otra para las filas con tenant_id null
-- (acciones de plataforma). Un advisory lock por cadena serializa las inserciones del mismo tenant y los
-- índices únicos (tenant_id, chain_seq) hacen imposible una rama. Ver "Integridad del audit_log" en §3.1.6.

inbound_messages (
  tenant_id fk, channel text not null,     -- 'telegram' | 'whatsapp'
  external_message_id text not null,       -- update_id (Telegram, único por bot) / message id (WhatsApp)
  received_at timestamptz not null default now(),
  primary key (tenant_id, channel, external_message_id)   -- deduplicación de reentregas, §2.6/§3.7
)
-- el Cron Job diario (§5.4.1) borra filas de más de 7 días: los canales reentregan
-- durante horas, no semanas

bot_sessions (
  tenant_id fk, channel text not null,      -- 'telegram' | 'whatsapp'
  channel_user_id text not null,
  state jsonb not null default '{}',        -- paso actual + lo elegido hasta ahora (§2.6)
  updated_at timestamptz not null default now(),
  primary key (tenant_id, channel, channel_user_id)
)
-- se descarta tras 1 hora sin actividad; anonymize_end_customer borra la sesión del cliente (§3.5)

oauth_state_nonces (
  tenant_id fk, nonce text not null,        -- 1 a 200 caracteres, aleatorio
  expires_at timestamptz not null, created_at timestamptz not null default now(),
  primary key (tenant_id, nonce)
)
-- un solo uso del `state` de OAuth de Google Calendar (§3.1.5): se escribe únicamente vía
-- consume_oauth_state_nonce() (SECURITY DEFINER, solo dashboard_service; exige contexto de tenant), que inserta
-- con on conflict do nothing y devuelve true la primera vez y false si ya se consumió o el state venció. Cada
-- llamada poda los vencidos. Sin grants a ningún rol de aplicación; no guarda datos personales.

notifications_log (
  id uuid pk, tenant_id fk, end_customer_id fk,
  channel text not null,   -- 'telegram' | 'whatsapp'
  template,
  status text not null default 'pending',   -- 'pending' (se crea así en el primer intento) | 'sent' | 'failed' (tras agotar reintentos, §6.4)
  retry_count int default 0,
  last_error text,   -- solo código/mensaje de error del proveedor, NUNCA el texto enviado; anonymize_end_customer lo pone en null (§3.5)
  sent_at,
  created_at timestamptz not null default now()   -- la pone la base (ningún rol de aplicación la escribe: grants por columna, §3.2); alimenta 'pending hace más de 1 hora' del visor (§2.11.7, §6.4)
)

payments (
  id uuid pk, tenant_id fk,
  payable_type text not null,   -- 'booking_deposit' | 'booking_pack'
  payable_id uuid,          -- id de bookings/booking_packs correspondiente
  provider text not null default 'mercadopago',   -- 'mercadopago'
  provider_payment_id text,
  amount numeric not null,
  status text not null,     -- 'pending' | 'approved' | 'rejected' | 'refunded'
  webhook_payload jsonb,    -- payload crudo del webhook. Se guarda completo para depurar problemas con Mercado Pago y como respaldo ante reclamos/contracargos (decisión de Franco); al anonimizar al end_customer se le quitan los datos del pagador (§3.5) — provider_payment_id se conserva y permite consultar el pago en Mercado Pago
  created_at,
  unique (provider, provider_payment_id),   -- idempotencia: un webhook reintentado (ej. por timeout) no puede procesarse dos veces
  check (amount >= 0)
)
```
**Hoy ningún rol de aplicación tiene grants sobre `payments`** (se le quitaron a `bot_service` en `20260103001200`: el cobro no está integrado, §7.1); quien lo integre (`platform_payments`, §4.3) define el rol y el grant que necesite, no se hereda del bot. `payments` es genérica y vive en el schema compartido (no en §7) porque `platform_payments` (§4.3) la usa desde cualquier producto — un pago siempre se registra acá antes de marcar el booking o el pack como pagado, sin importar de qué dominio venga. Mismo principio que la nota de RLS de §7: `tenant_id` acá **también** lo fuerza un trigger, no la aplicación — con la particularidad de que, al ser `payable_type`/`payable_id` polimórfico (no una FK única), el trigger necesita una rama por tipo (`bookings`, `booking_packs`) para resolver de qué tabla sacar el `tenant_id` real. Es justamente el tipo de tabla (dinero + límite de tenant) donde más importa no confiar en que la aplicación lo haga bien.

### 2.11 Panel admin de Franco (`platform_admin`)

Franco es un actor de plataforma real (§2.1), no solo alguien con acceso a scripts — necesita su propia app de administración.

- **Alcance**: **un panel admin por producto** (deploy separado por producto, apuntando cada uno a su propio proyecto Supabase), no un panel unificado — coherente con el aislamiento físico entre productos ya decidido (§1). La lógica backend (`admin-backend`, FastAPI) se construye una sola vez como el paquete `platform_admin` dentro de `platform/` (no 3 veces ad-hoc) ya que todos los productos la necesitan idéntica — ver §1.2/§4. El frontend (`admin-frontend`) parte de un template copiado una vez (§4.1/§5.4), no de un paquete instalado. Reusa `platform_auth`/`platform_tenancy`.
- **Acceso**: Franco tiene su propia cuenta en el Supabase Auth de cada proyecto, con un claim `is_platform_admin: true` inyectado vía Auth Hook (mismo mecanismo que los claims de `tenant_users`, ver §3.4). Este claim lo chequea `admin-backend` (vía `platform_auth`) como gate de autorización para aceptar el request — el bypass real a nivel de base de datos ocurre por el rol Postgres físicamente distinto (`platform_admin`/`platform_admin_readonly`, §3.1.3/§3.2), no por el claim en sí. No se usa el `service_role` key para esto.
- **MVP — funcionalidad requerida desde el arranque**:
  1. **CRUD de tenants**: alta (invoca `create_tenant`, §2.2), ver, suspender / levantar suspensión (§2.2), dar de baja, reactivar. "Reactivar" solo tiene sentido para `status='inactive'` (todavía en período de gracia, §2.2) — un tenant `'purged'` ya no tiene `tenant_users` ni `end_customers` reales para recuperar; "reactivarlo" sería en la práctica dar de alta uno nuevo reusando el mismo `slug`/fila.
  2. **Configuración de `platform_settings`**: ej. período de gracia de baja (§2.2), editable sin tocar código.
  3. **Visor de `audit_log`**: por tenant, para soporte/debugging. La verificación de integridad de la cadena (`verify_audit_chain`, §3.1.6) se corre desde el panel con el botón de verificar integridad, por tenant y para la cadena de plataforma.
  4. **Impersonación de solo lectura**: Franco puede ver el dashboard de un tenant como lo ve el tenant, para diagnosticar problemas de soporte — **sin permiso de escritura**. Se audita en el `audit_log` interno de Franco (`actor_type='platform_admin'`, `action='impersonate_view'`), pero **no se expone al tenant** (no aparece en su propio historial). `admin-backend` **reusa las mismas funciones de consulta de `dashboard-backend`** (compartidas dentro del repo del producto, no reimplementadas) con el rol `platform_admin_readonly` en vez de `dashboard_service` — evita mantener dos versiones de la misma lógica de lectura que con el tiempo terminan mostrando cosas distintas (§1.7.6). **Condición necesaria para que esto sea seguro**: esas funciones tienen que filtrar por `tenant_id` explícitamente en el código de la query (`WHERE tenant_id = :tenant_id`), no confiar solo en que RLS las scopee. `platform_admin_readonly` ya no bypassea el chequeo de tenant en RLS (§3.1.3): `readonly_tx` fija el tenant impersonado y la RLS lo limita igual que a `dashboard_service`; el filtro explícito queda como segunda barrera independiente de la RLS (defensa en profundidad).
  5. **Gestión de integraciones a nivel tenant** (`tenant_integrations` con `professional_id is null` — bot de Telegram en dev/test, número de WhatsApp Business en producción, §2.6/§4): Franco carga la credencial al dar de alta el tenant o al migrarlo de canal, vía `store_tenant_integration(tenant_id, integration_type, config, secret)` (`SECURITY DEFINER`, solo `platform_admin`); se guarda **cifrada en Supabase Vault**, `tenant_integrations` solo referencia un `secret_id` — la credencial en claro nunca se lee desde una tabla plana. **No incluye integraciones por-profesional** (Google Calendar): esas las conecta cada profesional vía OAuth desde el `dashboard-frontend` del propio tenant, no desde el panel admin.
  6. **Tracking manual de pago**: campo simple `paid_until`/`billing_notes` en `tenants`, sin integración de cobro (coherente con §1.3, monetización todavía abierta).
  7. **Visor de notificaciones con problemas**: lista de `notifications_log` con `status='failed'`, o en `'pending'` hace más de **1 hora** (un reintento perdido por un redeploy, §6.4 — los reintentos normales terminan en ~30 segundos), para que Franco detecte y resuelva manualmente turnos/confirmaciones que no llegaron a un end_customer. Muestra también las **limpiezas de Google Calendar fallidas** tras una anonimización (§7.1), para resolverlas a mano.
- **Fuera del MVP, explícitamente**: impersonación con escritura, panel unificado cross-producto — quedan en `PENDIENTESPORDEFINICION.md` si en algún momento hacen falta.
- **Configuración de campos accesorios de `end_customers`** (`tenants.settings`): la define **el propio tenant desde su dashboard**, no Franco desde el admin — el admin panel no gestiona esto.

---

## 3. Seguridad y aislamiento de datos

### 3.1 RLS — el límite real, nunca la única capa
1. **RLS activada en el 100% de las tablas**, sin excepción — ninguna tabla "de confianza implícita".
2. **Tests de aislamiento automatizados (pgTAP)**: por cada tabla con `tenant_id`, un test crea 2 tenants y verifica que tenant A no puede leer/escribir filas de tenant B. Corre en CI en cada PR — **una policy rota no puede mergear**.
3. **Contexto de tenant seteado explícitamente por request**, nunca confiado desde el body, y **con el mismo mecanismo para `bot_service` y `dashboard_service`** — ambos son procesos backend con conexión directa a Postgres (§3.1.5), no pasan por PostgREST, así que `auth.jwt()` nunca se puebla y no puede usarse como fuente de verdad para RLS. `dashboard-backend` verifica el JWT del usuario con `platform_auth`, extrae `tenant_id`, y recién ahí setea la misma variable de sesión que usa `bot_service`:
   ```sql
   -- seteado por platform_tenancy en cada transacción, en ambos backends
   set local app.current_tenant_id = '<uuid>';

   create policy tenant_isolation on bookings
     using (
       tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid  -- nullif: en una conexión reutilizada el GUC vale '' (no NULL) y ''::uuid lanzaría error
     );
   ```
   `platform_admin_readonly` **no** tiene bypass: queda sujeto a la misma policy que `dashboard_service`. La impersonación fija `app.current_tenant_id` del tenant que se mira (`readonly_tx` exige un tenant válido), así que el rol ve solo ese tenant y, sin contexto, 0 filas. `platform_admin` opera por policies explícitas propias (`platform_admin_acceso_total`), no por `current_user` dentro de `tenant_isolation`.
4. **RLS solo resuelve aislamiento por tenant, no permisos granulares** (ver §3.2 — decisión explícita de no meter el RBAC de §2.4 dentro de las policies, para no multiplicar la complejidad de testing en una arquitectura mantenida por un solo dev part-time).
5. **Esta decisión solo es válida si existe un punto server-side donde aplicarla**: el `dashboard-frontend` (Vite+React) **nunca llama directo al SDK de Supabase desde el navegador** — todo pasa por `dashboard-backend` (FastAPI, §5), que ahí chequea los `permission_key` de §2.4 antes de tocar la base. Sin este punto intermedio, la decisión de "permisos a nivel de aplicación" no tendría dónde vivir y habría que meterlos en RLS. **La misma regla aplica a `admin-frontend`**, que nunca habla directo con Supabase — todo pasa por `admin-backend`, aunque ahí no haya `permission_key` que chequear (el gate es el claim `is_platform_admin`, §2.11), por la misma lógica de mantener un único punto server-side auditable en vez de dos caminos distintos.

   **Login, MFA, invitaciones y vuelta de Google Calendar por proxy**: ambos backends exponen `/auth/*` (`login`, `mfa/enrolar`, `mfa/verificar`, `refrescar`, `logout`, `sesion`; el dashboard suma `invitacion/aceptar`) como proxy de Supabase Auth, definidos una sola vez como router de módulo en el paquete compartido `turnos_sesion` (`apps/shared/turnos_sesion/rutas_auth.py`); cada backend lo parametriza con un `ServiciosAuth` en `app.state.auth` (`rutas_auth.py` de cada app: cookies, límites, Turnstile, quién tiene acceso, auditoría) y el dashboard suma `invitacion/aceptar` en su propio router (el admin suma solo `auth_login_failed`). Los tokens de Supabase viajan al navegador **solo** en cookies `HttpOnly` + `Secure` + `SameSite=Strict` (`dash_*` en el dashboard, `adm_*` en el admin, para no mezclar sesiones; `COOKIE_SAMESITE` admite `lax`, `none` se rechaza) y nunca en el body de una respuesta: un XSS no puede robarlos. La cookie de refresh solo se envía a `/auth/*`. Un middleware (`MiddlewareSesionCookie` del paquete compartido `turnos_sesion`, que también emite y borra las cookies) convierte la cookie de acceso en `Authorization: Bearer` para que `platform_auth` siga siendo la única puerta, y como defensa CSRF en capas exige `Origin` igual al del frontend en todo método que escribe (además de `SameSite` y CORS acotado). **Auditoría de autenticación**: ambos backends escriben en `audit_log` (`entity='auth'`, sin email, IP, contraseña ni token en la metadata) el login correcto, el alta y la verificación de MFA, el código TOTP inválido (`auth_mfa_failed`: el actor ya está identificado; la metadata no lleva el código ni el factor), la invitación aceptada del dashboard, el refresh y el logout (`auth_login_succeeded`, `auth_mfa_enrolled`, `auth_mfa_verified`, `auth_mfa_failed`, `auth_invitation_accepted`, `auth_session_refreshed`, `auth_logged_out`); el admin suma `auth_login_failed` (actor `platform_admin`, sin tenant, solo un código). Es de mejor esfuerzo: si escribir el audit falla, se loguea sin PII y el flujo de autenticación sigue. **El login fallido del dashboard no va a `audit_log`** (decisión de Franco): un intento con un email cualquiera no pertenece a ningún local, no hay `tenant_id` al que atribuirlo, y abrir esa escritura sin tenant permitiría llenar la tabla desde afuera; queda solo en los logs estructurados (`auth_login_failed`, sin email, IP ni token, §6.3) y en los logs de Supabase Auth. **Límites de intentos**: el login se limita por IP y por email en ambos backends (el email solo cuenta los fallos por credenciales inválidas); el refresh tiene su propio límite por IP (`LOGIN_LIMITE_REFRESCO`) y responde 429 con `Retry-After`. La IP real sale de `LOGIN_IP_HEADER` y `LOGIN_PROXIES_CONFIABLES`, validadas una sola vez al arrancar (valores en `docs/deploy.md`). Si el JWKS de Supabase no está disponible, el logout igual cierra la sesión (204) y omite solo el registro en `audit_log`. Consecuencia de despliegue: con `Strict`, frontend y backend tienen que ser el mismo *sitio* (subdominios del mismo dominio). **Vuelta de Google Calendar**: Google redirige por una navegación entre sitios y `Strict` no manda la cookie en ese salto, así que `GOOGLE_OAUTH_REDIRECT_URI = <DASHBOARD_ORIGIN>/calendar/callback` — una página del `dashboard-frontend` que hace `POST /calendar/callback` con `{code, state}` al backend (mismo sitio, sí lleva cookie); solo ese POST (con chequeo de `Origin`) completa la conexión; el `GET /calendar/callback` del backend no escribe ni llama a Google, solo redirige al dashboard. El `state` va firmado, atado al usuario de la sesión que inicia la conexión y con un nonce de un solo uso (`consume_oauth_state_nonce`): un `state` reusado o de otro usuario se rechaza como `state_invalido` (`apps/dashboard-backend/dashboard_backend/rutas_calendar.py`).
6. **Policies de las tablas sin `tenant_id` directo (o con `tenant_id` nullable)** — "100% de las tablas" incluye estas:

   | Tabla | Policy |
   |---|---|
   | `tenants` | `bot_service`/`dashboard_service`: solo `id = app.current_tenant_id`. Roles de plataforma: todas. |
   | `permissions` | Lectura para todos los roles de aplicación (catálogo, sin datos de tenant). |
   | `platform_settings`, `platform_admins` | Solo `platform_admin`. El resto no tiene policy → denegado por defecto. |
   | `audit_log` | `dashboard_service`: policy de su tenant **y** `actor_type <> 'platform_admin'` (§2.11.4), aunque hoy el rol no tiene `SELECT` sobre la tabla (`20260103001200`: defensa en profundidad si se le volviera a conceder). `platform_admin`: todo. |
   | `inbound_messages` | `bot_service`: su tenant. |
   | `oauth_state_nonces` | Policy `tenant_isolation` por defensa en profundidad; ningún rol de aplicación tiene grants sobre la tabla, solo `dashboard_service` ejecuta `consume_oauth_state_nonce` (§2.10). |
   | `tenant_users`, `tenant_integrations` | Además de la policy por tenant: policy explícita `to platform_admin` (todas las filas) — `create_tenant` (§2.2) y la carga de credenciales (§2.11.5) operan sin contexto de tenant. Sin `BYPASSRLS`: cada acceso de plataforma es una policy visible y testeable. |
   | `notifications_log` | Además de la policy por tenant: `SELECT` para `platform_admin` en todas las filas — alimenta el visor de §2.11.7 (metadatos operativos; `last_error` no lleva el texto enviado, §2.10). |

   Cada una con su test pgTAP positivo y negativo (§3.3).

   **Integridad del `audit_log` (cadena de hashes).** Que ningún rol de aplicación pueda `UPDATE`/`DELETE` no protege contra quien administra la base (el rol dueño o un superusuario puede deshabilitar el trigger de solo-agregar y editar filas sin dejar rastro). Por eso cada fila nueva lleva `chain_seq`, `prev_hash` y `hash` (§2.10), que calcula un trigger `BEFORE INSERT` — cubre `write_audit_log`, `audit_log_append` y cualquier `INSERT` directo, y sobrescribe lo que mande quien inserta. `verify_audit_chain(tenant_id)` (`SECURITY DEFINER`, `EXECUTE` **solo para `platform_admin`**; con `tenant_id` null verifica la cadena de plataforma) recorre la cadena y devuelve el id de la primera fila rota (hash que no coincide con el contenido, `prev_hash` distinto del hash anterior o salto en `chain_seq`), o `null` si es íntegra; a la escala de §1.1 es inmediato. La llaman `GET /tenants/{tenant_id}/audit-log/verificar-cadena` y `GET /plataforma/audit-log/verificar-cadena` del `admin-backend` (solo `platform_admin` con MFA `aal2`; responden `{tenant_id, integra, fila_invalida_id, fila_invalida_chain_seq}`; 404 `tenant_no_encontrado` si el tenant no existe) y el panel admin las muestra en la pantalla de auditoría. **Límites aceptados**: (1) **borrar la cola** de una cadena (las últimas filas) no se detecta, porque no queda ninguna fila posterior que delate el hueco; detectarlo exige anclar afuera el último hash y compararlo: el job de purga emite en cada corrida un log `audit_chain_anchor` con la cabeza de cada cadena (`audit_chain_heads()`, ver Google Calendar en §3.5), de modo que los logs de Render sirven de ancla, pero la comparación automática contra la cadena actual no existe; (2) quien tenga el **rol dueño** y pueda recalcular toda la cadena desde el punto alterado puede forjarla: la verificación detecta ediciones parciales, no a un adversario que rehace los hashes. Las filas anteriores a la migración se encadenaron en bloque al aplicarla ("génesis"): la cadena protege lo escrito desde ahí, no lo anterior.

   **Qué se audita** (la metadata lleva nombres de campos, nunca valores ni datos personales): en `dashboard-backend`, turnos (alta, aprobar, rechazar, cancelar, completar, mover, y `calendar_sync_failed`) y packs; servicios y profesionales (alta, edición, baja, desactivación) y `professional_services` (`professional_service_assigned`/`_updated`/`_removed`); **disponibilidad** (`availability_rule_*` y `availability_block_*`); **clientes** (`customer_created`, `customer_updated`, `anonymize_end_customer`); `settings_updated`; staff (permisos, owner, activación) y el vínculo profesional-cuenta (§2.9); los eventos de autenticación de ambos backends (§3.1.5); y en la plataforma, `impersonate_view` (§2.11.4) y los pasos de la purga `tenant_purge_step` (§2.2). Los cambios de datos se auditan **en la misma transacción** que el cambio (si el registro falla, el cambio se revierte); la excepción son los eventos de autenticación, de mejor esfuerzo.

### 3.2 Roles Postgres — grants concretos por proyecto

*Cada producto es su propio proyecto Supabase (§1) con su propio schema — los grants de `bot_service` son sobre las tablas de dominio de **este** producto (§7). Acá se ilustran las tablas de turnos.*

| Rol | Usado por | Grants |
|---|---|---|
| `bot_service` | `apps/bot` (FastAPI) — Telegram en dev/test, WhatsApp en producción (§2.6) | **Turnos**: `SELECT` en `services`/`professionals`/`professional_services`/`availability_rules`/`availability_blocks`/`booking_packs`; `SELECT`/`INSERT`/`UPDATE` en `bookings` (el `SELECT` hace falta para calcular huecos y "mis próximos turnos"); `UPDATE` **solo de la columna `used_sessions`** en `booking_packs` (consumir/devolver sesión). `SELECT`/`INSERT`/`UPDATE` en `end_customers` ("mis datos", deduplicación §2.8). `SELECT` en `notifications_log` y `EXECUTE` en `record_notification_attempt` (envíos y reintentos, §6.4): **sin `INSERT`/`UPDATE` directo** sobre la tabla, solo por la función (migración `20260103001300`; `created_at` la pone la base, §2.10). **Sin acceso a `payments`** (migración `20260103001200`: el webhook de Mercado Pago, §2.10/§4.3, no lo escribe este rol). `INSERT` en `inbound_messages` (§2.6). `SELECT`/`INSERT`/`UPDATE`/`DELETE` en `bot_sessions` (§2.6). `SELECT` de **su propia fila** de `tenants` (para leer `settings`). `EXECUTE` en `resolve_inbound_tenant`/`find_integration`/`get_integration_secret`/`find_dormant_end_customer`/`anonymize_end_customer`/`calendar_scrub_targets`. **Sin** `SELECT` en `tenant_integrations`. Sin `DELETE` en ningún otro caso (`bot_sessions` es la única excepción: es estado efímero de conversación, no un registro del negocio). Sin escritura directa a `tenant_users`/`audit_log` (se escribe vía función `SECURITY DEFINER`). |
| `dashboard_service` | `dashboard-backend` (FastAPI), JWT de `tenant_users` | Scopeado por `tenant_id` vía RLS, grants por tabla (no "CRUD normal"): **`tenants`**: `SELECT` de su fila y `UPDATE` **solo de la columna `settings`** — nunca `plan`/`status`/`paid_until`/`billing_notes`/`deactivated_at` (tracking de cobro y ciclo de vida son de Franco). **`tenant_users`**: `SELECT`/`INSERT`/`UPDATE` por columna (todas menos `pending_invite_email`, §2.7), sin `DELETE` (§2.9). **`tenant_user_permissions`**: `SELECT`/`INSERT`/`DELETE`; **`permissions`**: `SELECT`. **`end_customers`**: `SELECT`/`INSERT`/`UPDATE`, sin `DELETE` (borrar = `anonymize_end_customer`, §3.5). **Tablas de §7**: CRUD, salvo `DELETE` en `bookings`/`booking_packs` — se cancelan, no se borran (conserva el historial de facturación/ocupación y evita que un pack con sesiones usadas desaparezca). **`tenant_integrations`**: `SELECT` sin las columnas `secret_id`/`config`; el OAuth de Google por profesional se guarda vía una función `SECURITY DEFINER`, sin escritura directa. **`audit_log`**: **sin `SELECT` ni escritura directa** (el `SELECT` se revocó en `20260103001200`; la lee `platform_admin`, §3.1.6), escritura solo vía `write_audit_log()` (`EXECUTE` para `bot_service`, `dashboard_service`, `platform_admin` y `purge_job` — este último registra `calendar_scrub_failed` del paso 0 de §2.2, que corre fuera de `purge_tenant_data`). **`notifications_log`**: `SELECT`; los avisos de aprobado/rechazado (§2.6) se registran con `record_notification_attempt`, sin `INSERT`/`UPDATE` directo (migración `20260103001300`). `EXECUTE` en `anonymize_end_customer`/`find_integration`/`get_integration_secret`/`calendar_scrub_targets` (avisos y sync de Calendar al aprobar/mover/cancelar, §7.1) y en `get_tenant_staff_identity` (nombre y email del staff de su tenant, §2.9), `EXECUTE` en `release_expired_pending_overlaps` (permiso otorgado en `20260103003200` para que el alta de turno del dashboard pueda liberar pendientes vencidos solapados, §7.1; exige contexto de tenant) y `EXECUTE` en `consume_oauth_state_nonce` (nonce de un solo uso del `state` de OAuth de Calendar, §3.1.5; la tabla `oauth_state_nonces` no está concedida). **El chequeo de permisos granulares (§2.4) ocurre en `platform_auth`, antes de invocar la query** — no en la policy. |
| `platform_admin` | Panel admin de Franco (§2.11), claim `is_platform_admin: true` | Write en `tenants`/`platform_settings`/`tenant_users` (esto último para crear el primer owner en `create_tenant`, §2.2); lectura de `audit_log` y `notifications_log` cross-tenant (§3.1.6); Sobre `platform_admins` solo `SELECT` (valida la sesión; altas y bajas de admins se hacen fuera de la app, migración `20260103001200`). `EXECUTE` en `store_tenant_integration`, `calendar_scrub_targets`, `get_tenant_staff_identity` (owners de cada tenant, §2.9) y `verify_audit_chain` (integridad del `audit_log`, §3.1.6; es el único rol que la ejecuta); **sin** `EXECUTE` sobre `get_integration_secret` ni `anonymize_end_customer` (revocados en `20260103001200`: escribe secrets, no los lee). Las transiciones de `tenants.status` válidas (§2.2) las hace cumplir un **trigger**, con test pgTAP. **No tiene acceso directo a datos operativos de un tenant** (bookings, end_customers) — para eso existe el siguiente rol. |
| `platform_admin_readonly` | Sesión de impersonación (§2.11) | **Solo `SELECT`**, sin ningún grant de escritura, sobre las tablas operativas de un tenant. Es un rol Postgres físicamente distinto — aunque el código de la app tenga un bug, la base de datos rechaza cualquier `INSERT`/`UPDATE`/`DELETE` mientras se impersona. |
| `purge_job` | Render Cron Job de purga (§5.4.1) | **Sin grants sobre tablas.** Solo `EXECUTE` en `unlink_platform_admin_accounts` (desvincula del tenant en `purging` las cuentas que figuran en `platform_admins`, para que la purga no las borre de Auth), `list_tenants_due_for_purge` (calcula en la base, con `platform_settings`, qué tenants vencieron y hace cuántos días, más los que ya están `purging` para retomarlos — devuelve solo ids y días), `begin_tenant_purge` (primer paso de la cascada de §2.2: `inactive`→`purging`), `calendar_scrub_targets`/`get_integration_secret` (paso 0 de §2.2, con el contexto del tenant que purga), `purge_tenant_data`, `mark_tenant_purged` y `prune_inbound_messages` (funciones `SECURITY DEFINER` que re-validan sus precondiciones en la base, §2.2). |
| `scheduler_job` | Render Cron Job de vencimiento de turnos y sesiones (§5.4.1) | `EXECUTE` en `expire_pending_bookings`, `prune_bot_sessions`, `claim_notification_outbox`, `resolve_outbox_delivery`, `complete_notification_outbox` y `prune_notification_outbox` (funciones `SECURITY DEFINER`); para avisar al cliente, con el contexto de tenant seteado por cada turno vencido: `EXECUTE` en `find_integration`/`get_integration_secret` y en `record_notification_attempt`. **Sin grants directos sobre `notifications_log`** (ni sobre ninguna tabla, igual que `purge_job`): el aviso se registra con `public.record_notification_attempt`, función `SECURITY DEFINER` que exige el contexto de tenant, comprueba que el `end_customer` sea de ese tenant (inexistente y ajeno dan el mismo error), crea la fila o actualiza el intento y trata `sent`/`failed` como finales; `last_error` se trunca a 500 caracteres. **Por qué una función y no `INSERT`/`UPDATE`**: un rol sin acceso a tablas no puede leer ni tocar nada fuera de lo que la función valida (migración `20260102000300_turnos_jobs.sql`). Nada más. **Maneja un dato personal**: `expire_pending_bookings` le devuelve el `channel`/`channel_user_id` del cliente a avisar (§5.4.1). |
| `service_role` (bypass total de RLS) | Migraciones y scripts internos; el puñado de operaciones que llaman a la Auth Admin API de Supabase: invitar al owner (`admin-backend`, `create_tenant` §2.2) e invitar staff (`dashboard-backend`, §2.9); **y** el Cron Job de purga, **solo** para eliminar vía Auth Admin API las cuentas que le devolvió `purge_tenant_data` (§2.2) | **Nunca** para procesar input externo arbitrario/no confiable (un webhook, un body de request sin validar). Sus usos son excepciones explícitas y acotadas: en `admin-backend`, operaciones gateadas por el claim `is_platform_admin` ya verificado (§2.11); en el Cron Job, una lista que sale de la propia base ya validada, no de input externo. En todos los casos la necesitan porque la Auth Admin API no acepta ningún otro tipo de credencial. **`service_role` no tiene permisos de tabla sobre `public`**: nada de la aplicación la usa para leer o escribir datos; los scripts de setup (`scripts/setup_proyecto.py`: primer `platform_admin`, clave HMAC en Vault, contraseñas de los roles de login) corren con el rol dueño de las migraciones (`SETUP_DATABASE_URL`, conexión directa a Postgres), no con `service_role`. **Una clave `sb_secret_…` propia por servicio** (dashboard, admin-backend, job de purga); el alcance real de la clave del dashboard y el riesgo aceptado están en §3.7. |

**Grants por columna vigentes** (una columna futura exige un grant explícito: no se filtra por accidente): `tenant_users.pending_invite_email` (§2.9) la lee y escribe solo `platform_admin` — `dashboard_service` y `platform_admin_readonly` tienen los grants de todas las columnas menos esa (el segundo solo `SELECT`) —, y `notifications_log.created_at` (marca de creación que usa el visor de §2.11.7 para "pending hace más de 1 hora") la pone la base: `bot_service` y `dashboard_service` no tienen `INSERT`/`UPDATE` sobre la tabla (ni por columna: escriben por `record_notification_attempt`), así la aplicación no puede escribirla ni falsearla (migraciones `20260103000200`, `20260103000500` y `20260103001300`).

**Funciones internas sin `EXECUTE`**: `anonymize_end_customer_core` (y `calendar_scrub_targets_core`) recibe el actor ya decidido, corre como invocador y no tiene `EXECUTE` para ningún rol; solo la alcanzan las funciones `SECURITY DEFINER` que derivan el actor de `session_user` (`anonymize_end_customer`, `purge_tenant_data`, §3.5). Así pgTAP prueba sus rechazos pasando el actor como parámetro, y desde una conexión de aplicación no se puede invocar con un actor falseado.

**Cada backend se conecta con su propio rol de login** (`bot_service`, `dashboard_service`, `platform_admin`, `platform_admin_readonly`, `purge_job`, `scheduler_job`), no con un rol compartido + `SET ROLE` — así las funciones `SECURITY DEFINER` pueden identificar al caller real por `session_user` (§3.5).

**Escenario de credencial de rol comprometida (supuesto explícito).** El aislamiento por tenant de `bot_service` y `dashboard_service` depende de `app.current_tenant_id`, un GUC de sesión que el propio proceso setea en cada transacción (§3.1.3). Una sesión que ejecute SQL arbitrario con ese rol puede reescribirlo (`set app.current_tenant_id = '<otro uuid>'`) y la policy `tenant_isolation` lo acepta. Por eso se asume que **la credencial de login de un rol de aplicación filtrada, o una inyección SQL en el servicio que la usa, equivale a acceso a los datos de todos los tenants que ese rol puede ver**; RLS protege contra bugs de aplicación (una query sin filtro, un `tenant_id` equivocado), no contra quien controla la sesión. Eso vale también para `platform_admin_readonly`, que desde la migración `20260103001100` no tiene bypass pero igual depende del GUC. Lo que limita el daño, y es lo que se mantiene:
- **Una credencial por servicio, distinta y rotable.** Cada backend usa su propio rol de login (arriba), así que filtrar una no entrega las demás ni los permisos de otro rol. Rotar una credencial es cambiar la contraseña de ese rol y la variable de entorno del servicio (gateado como `credenciales`); no hay credencial compartida que obligue a rotar todo junto.
- **Grants mínimos.** Cada rol solo conserva lo que su código usa; en la última revisión se recortaron: `bot_service` sin `payments` y sin `INSERT`/`UPDATE` directo sobre `notifications_log` (`20260103001200`, `20260103001300`), `dashboard_service` sin `audit_log` ni escritura directa en `notifications_log`, `platform_admin` sin `INSERT`/`DELETE` en `platform_admins` ni `EXECUTE` sobre `get_integration_secret`/`anonymize_end_customer`, y `EXECUTE` revocado a todo rol sobre las funciones trigger. Las funciones `SECURITY DEFINER` (todas con `search_path` vacío) acotan lo que se puede hacer sin acceso a tablas (`purge_job` y `scheduler_job` no tienen ninguno).
- **Secrets de Vault: quién puede leerlos.** `vault.decrypted_secrets` no está concedido a ningún rol de aplicación; solo se alcanza vía `get_integration_secret(uuid)` (`SECURITY DEFINER`), con `EXECUTE` hoy para **`bot_service`, `dashboard_service`, `scheduler_job` y `purge_job`**, cada uno por lo que usa (enviar mensajes, sync de Calendar, avisos del job de vencimiento, paso 0 de la purga). La función exige un contexto de tenant y que la integración sea de ese tenant, **pero ese contexto es el GUC reescribible**: con cualquiera de esos cuatro roles comprometidos, un atacante que conozca o enumere ids de integración puede leer los tokens de cualquier tenant. `platform_admin` ya no tiene ese `EXECUTE` (`20260103001200`) y solo escribe secrets (`store_tenant_integration`). La clave HMAC de pseudonimización (§2.3) está en Vault, pero `pseudonymize_channel_user` no tiene `EXECUTE` para ningún rol de aplicación. `service_role` no tiene permisos de tabla sobre `public`.
- **Los demás roles se tratan igual.** La credencial de `scheduler_job`, `purge_job` y `platform_admin` también equivale a acceso total a todo lo que su rol puede ver o ejecutar: `scheduler_job` y `purge_job` no tocan tablas pero pueden ejecutar sus funciones `SECURITY DEFINER` (vencer turnos de cualquier tenant, `purge_tenant_data` sobre cualquier tenant que cumpla las precondiciones, leer secrets vía `get_integration_secret`); `platform_admin` ya opera cross-tenant por diseño (escribe `tenants`, `platform_settings`, `tenant_users`, carga secrets). Para ninguno hay aislamiento por tenant que contenga el daño.
- **Alertas y detección (solo lo que existe hoy).** Sentry (§6.1) en backends y cron jobs captura excepciones inusuales, el monitor de cron de Sentry avisa si el job de purga diaria o el de vencimiento no corrió o tardó de más, y el email de fallo de Render avisa si falla la ejecución de cualquiera de los dos jobs (§6.2), UptimeRobot avisa por email si un servicio cae (§6.2), y los logs estructurados de Render (§6.3) y `audit_log` registran cada anonimización, paso de purga y acción de plataforma. No hay hoy una alerta automática por uso anómalo de un rol (consultas masivas, cambio de GUC a otros tenants): la detección depende de revisar esos logs y `audit_log`, y es parte del riesgo aceptado abajo.
- **Respuesta ante una sospecha**: rotar la contraseña del rol afectado y su variable de entorno; si el rol es uno de los cuatro con acceso a `get_integration_secret`, rotar también los tokens de las integraciones expuestas (bot de Telegram/WhatsApp, Google Calendar) y la clave HMAC si hubo duda sobre ella; revisar `audit_log` y los logs del proveedor de base.
- **Riesgo aceptado y no mitigado hoy**: no hay credencial por tenant ni un contexto de tenant que la sesión no pueda reescribir; cerrarlo exigiría una conexión por tenant o policies atadas a una identidad que el rol no controle, y se decidió no hacerlo en esta etapa.

### 3.3 Test de aislamiento (pgTAP) — el que bloquea el merge

```sql
select plan(1);
insert into tenants (id, name, slug) values ('11111111-1111-1111-1111-111111111111','A','a');
insert into tenants (id, name, slug) values ('22222222-2222-2222-2222-222222222222','B','b');

set local role bot_service;
set local app.current_tenant_id = '11111111-1111-1111-1111-111111111111';

select is(
  (select count(*) from bookings where tenant_id = '22222222-2222-2222-2222-222222222222'),
  0::bigint,
  'tenant A no puede ver bookings de tenant B'
);
```
Se repite para cada tabla con `tenant_id`, y para el rol `platform_admin_readonly` se agrega un test que verifica que un `INSERT`/`UPDATE` explícito falla con permission denied. Para las tablas hijas con `tenant_id` forzado por trigger (`payments`, `tenant_user_permissions`, etc. — el patrón no es exclusivo de §7, ver nota de §7), se agrega además un test que intenta insertar/actualizar la fila con un `tenant_id` **deliberadamente distinto** al de su padre, y verifica que el trigger lo corrige al valor real — sin este test, nada garantiza que el trigger esté efectivamente implementado (o que cubra `UPDATE` además de `INSERT`). Mismo criterio para los `check` de integridad de §7 (stock/balance/precios/rangos no negativos, `end_time > start_time`, etc.): cada uno tiene un test que intenta insertar el valor inválido y espera que la base lo rechace — un `check` sin test negativo tiene el mismo problema de confianza que un trigger sin test. Se suman además: los tests negativos de `anonymize_end_customer` (§3.5), el test del Auth Hook (§3.4), los de las policies de tablas sin `tenant_id` (§3.1.6), los de los grants por columna de `dashboard_service` (ej. no puede `UPDATE tenants set status`, §3.2) y los de los triggers de invariantes (último owner, último `platform_admin`, transiciones de `tenants.status`). Y también: `resolve_inbound_tenant` no devuelve tenants `suspended`/`inactive`/`purged`; `get_integration_secret` y `find_integration` rechazan integraciones de otro tenant; `purge_tenant_data` rechaza un tenant `active` o con el período sin vencer; `purge_job` y `scheduler_job` no pueden hacer `SELECT` sobre ninguna tabla; `expire_pending_bookings` cancela solo lo vencido; el trigger de devolución de sesión de pack devuelve exactamente una sesión por booking cancelado, incluso con varios bookings del mismo pack cancelados en un solo `UPDATE`; el `check` de `bookings.pending_expires_at`; `audit_log` rechaza `UPDATE`/`DELETE` desde cualquier rol; la deduplicación de `inbound_messages`; las FKs compuestas rechazan referencias cruzadas entre tenants; la validación de `booking_approval_timeout_hours` y de los límites de reserva del bot (§2.10); el rechazo de las transiciones desde `purging` y de una gracia menor a 30 días (§2.2); `consume_oauth_state_nonce` (un solo uso, aislado por tenant, sin contexto falla); la cadena de hashes del `audit_log` y `verify_audit_chain` (§3.1.6); la ausencia de `view_reports` en el catálogo; y que `platform_admin_readonly` sin contexto de tenant ve 0 filas y con contexto solo las de ese tenant (§3.1.3). **Meta-test de funciones `SECURITY DEFINER`** (`44_funciones_plataforma_grants.sql`): recorre **todas** las funciones `SECURITY DEFINER` de `public` por `pg_proc` (no una lista de las que ya existen) y exige, para cada una, `search_path` vacío, ningún `EXECUTE` para `PUBLIC`/`anon`/`authenticated`/`service_role` y que su `EXECUTE` coincida exactamente con un allowlist explícito del propio test: una función definer nueva sin entrada en el allowlist hace fallar el CI, y quien la crea tiene que declarar ahí qué roles pueden ejecutarla. Los tests que dependen del caller real usan `set session authorization <rol>` (cambia `session_user`); `set role` no alcanza.

### 3.4 Claims custom en el JWT (Auth Hook)

El JWT lleva solo identidad, nunca permisos cacheados (para no arrastrar un permiso revocado en una sesión vieja): `tenant_id`, `is_owner`, `is_platform_admin`. Los `permission_key` de §2.4 se consultan en vivo contra `tenant_user_permissions` en cada request desde `platform_auth`. **Este JWT lo verifica y decodifica `platform_auth` en `dashboard-backend`** (no se lee vía `auth.jwt()` a nivel de RLS, §3.1.3) — a partir de su `tenant_id` es que `dashboard-backend` setea `app.current_tenant_id` antes de tocar la base.

```sql
create or replace function custom_access_token_hook(event jsonb)
returns jsonb as $$
declare
  v_user uuid := (event->>'user_id')::uuid;
  v_tenant_id uuid; v_is_owner boolean;
begin
  select tenant_id, is_owner into v_tenant_id, v_is_owner
  from tenant_users where auth_user_id = v_user and is_active;
  -- Franco (no está en tenant_users) o staff desactivado (§2.9) → sin fila. Se arma
  -- con jsonb_build_object, que convierte el NULL de SQL en un null de JSON: con
  -- jsonb_set(..., to_jsonb(NULL)) el resultado entero sería NULL y el token saldría roto.
  -- platform_auth trata un tenant_id null como sesión no autorizada para dashboard-backend
  -- (staff desactivado queda bloqueado); admin-backend exige is_platform_admin
  return jsonb_set(event, '{claims}', (event->'claims') || jsonb_build_object(
    'tenant_id', v_tenant_id,
    'is_owner', coalesce(v_is_owner, false),
    'is_platform_admin', exists (select 1 from platform_admins where auth_user_id = v_user)));
end;
$$ language plpgsql stable security definer set search_path = public, pg_temp;
-- EXECUTE solo para supabase_auth_admin; revocado de public/anon/authenticated
```
`is_owner` en el JWT se usa **solo** para la regla de MFA de §3.6. El chequeo de escalación de privilegios (§2.9) y el estado del tenant (§2.2) se leen en vivo. Test pgTAP del hook para tres casos — staff activo, staff desactivado, Franco sin fila en `tenant_users` — verificando que los claims nunca salen NULL.

### 3.5 Función de anonimización (derecho al olvido, §1.5/§2.3)

Única función, invocable por `bot_service` (autoservicio del end_customer) y `dashboard_service` (el tenant), y alcanzada por la purga de tenant de forma anidada desde `purge_tenant_data` (§2.2, ver grants abajo). `platform_admin` ya **no** tiene `EXECUTE` (se lo quitó la migración `20260103001200`: nada del panel admin la llama), aunque la rama de actor `platform_admin` de la función sigue existiendo por si se vuelve a conceder — misma lógica sin importar quién la dispara. **Es `SECURITY DEFINER`, así que bypassea RLS por diseño — necesita su propio chequeo de tenant interno**, si no cualquier caller con permiso de ejecutarla podría anonimizar un `end_customer` de otro tenant pasándole cualquier `id`. **El actor nunca se toma de un parámetro**: se deriva del rol real que llamó (`session_user` — dentro de una función `SECURITY DEFINER`, `current_user` es el dueño de la función), porque un parámetro lo podría falsificar cualquier caller para hacerse pasar por `platform_admin` y cruzar tenants (§1.7.3). El chequeo de tenant exige además que `app.current_tenant_id` no sea `NULL` — si no, `NULL <> v_tenant_id` no dispararía la excepción (`NULL` se trata como `false` en un `IF`):

```sql
create or replace function anonymize_end_customer(p_id uuid)
returns void as $$
declare
  v_tenant_id uuid; v_channel text; v_channel_user_id text;
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_actor_type text;
begin
  -- dentro de SECURITY DEFINER, current_user es el DUEÑO de la función; el rol que
  -- llamó es session_user (cada backend se conecta con su propio rol de login, §3.2)
  v_actor_type := case session_user
    when 'bot_service' then 'bot'          when 'dashboard_service' then 'staff'
    when 'platform_admin' then 'platform_admin'  when 'purge_job' then 'system'  -- 'platform_admin' hoy sin EXECUTE; 'system' vía purge_tenant_data
  end;
  if v_actor_type is null then
    raise exception 'rol no autorizado para anonimizar: %', session_user;
  end if;

  select tenant_id, channel, channel_user_id into v_tenant_id, v_channel, v_channel_user_id
  from end_customers where id = p_id for update;
  if not found then raise exception 'end_customer inexistente'; end if;

  -- solo platform_admin y el job de purga cruzan tenants; el resto necesita
  -- un tenant verificado Y que coincida
  if v_actor_type not in ('platform_admin', 'system')
     and (v_caller_tenant is null or v_caller_tenant <> v_tenant_id) then
    raise exception 'tenant mismatch: no se puede anonimizar un end_customer de otro tenant';
  end if;

  update end_customers
  set phone = null, name = null, extra_data = '{}'::jsonb, deleted_at = now(),
      channel_user_hash = case when v_channel_user_id is null then channel_user_hash  -- idempotente
                               else pseudonymize_channel_user(v_tenant_id, v_channel, v_channel_user_id) end,
      channel_user_id = null
  where id = p_id;

  -- copias de PII fuera de end_customers (§2.10). La rama de payments referencia
  -- tablas de dominio: vive en la migración del producto, como el trigger de pagos (§4.2)
  update payments
  set webhook_payload = webhook_payload - 'payer' #- '{additional_info,payer}' #- '{card,cardholder}'
  where tenant_id = v_tenant_id and (
    (payable_type = 'booking_deposit' and payable_id in (select id from bookings where end_customer_id = p_id))
    or (payable_type = 'booking_pack' and payable_id in (select id from booking_packs where end_customer_id = p_id)));
  update notifications_log set last_error = null where end_customer_id = p_id;
  -- turnos futuros pendientes: se cancelan (rama de dominio, §4.2); la sesión del
  -- pack la devuelve el trigger de bookings (§7.1), no esta función
  update bookings set status = 'cancelled', pending_expires_at = null
  where end_customer_id = p_id and status = 'pending' and lower(time_range) > now();
  delete from bot_sessions
  where tenant_id = v_tenant_id and channel = v_channel and channel_user_id = v_channel_user_id;

  insert into audit_log (tenant_id, actor_type, actor_id, action, entity, entity_id, metadata)
  values (v_tenant_id, v_actor_type, v_actor_id, 'anonymize_end_customer', 'end_customers', p_id,
          jsonb_build_object('fields_anonymized', array['phone','name','extra_data','channel_user_id',
                                                         'payments.payer','notifications_log.last_error']));
end;
$$ language plpgsql security definer set search_path = public, pg_temp;
```
`actor_id` sale de `app.current_actor_id`, que `platform_tenancy` setea desde la identidad ya verificada (el `tenant_user` del JWT, el `end_customer` en el bot, el `auth_user_id` de Franco) — misma confianza que el contexto de tenant. Lo que **no** se puede falsificar es el rol, que es lo que define qué se permite.

**Turnos futuros del cliente anonimizado** (decisión de Franco): la misma función pasa a `cancelled` sus bookings futuros en `pending` (liberando el horario; la sesión del pack, si usaban una, la devuelve el trigger de §7.1); los `confirmed` quedan, y el dashboard los marca "sin cliente identificable" (derivable de `end_customers.deleted_at`, sin columna nueva) para que el negocio decida.

**Google Calendar** (fuera de la base): después del commit, el backend que anonimizó obtiene con `calendar_scrub_targets(tenant_id, end_customer_id)` (`SECURITY DEFINER`; sin `end_customer_id` devuelve todos los del tenant, para la purga §2.2. Igual que `anonymize_end_customer`, para `bot_service`/`dashboard_service` exige que `tenant_id` coincida con `app.current_tenant_id`; para `purge_job` re-valida en la base que el tenant esté `inactive` con el período vencido — el paso 0 corre antes de `purge_tenant_data` y no puede depender de su validación. Tests pgTAP de ambos rechazos) los pares `(integration_id, google_calendar_event_id)`, y `platform_calendar` edita todos esos eventos — pasados y futuros — para quitarles nombre y teléfono (§7.1). Si no puede (el profesional se fue y su conexión se eliminó, desconectó Google, o el token venció), registra `calendar_scrub_failed` en `audit_log` con el `google_calendar_event_id` (sin PII) y aparece en el panel admin (§2.11.7). Esas limpiezas se reintentan: `list_calendar_scrubs_pending()` (`SECURITY DEFINER`, solo `purge_job`) devuelve una fila por evento pendiente (`tenant_id`, `integration_id`, `event_id`, `attempts`, `first_failed_at`, `last_failed_at`, `last_motivo`; sin PII) y `record_calendar_scrub_result(tenant_id, event_id, resuelta, motivo)` registra en `audit_log` `calendar_scrub_resolved` o `calendar_scrub_retry_failed`; una resuelta no vuelve a listarse y un fallo posterior la reabre. La lista devuelve primero las que tienen integración (la más vieja primero) y después las de `integration_id` nulo, que no se pueden reintentar y no desplazan a las nuevas del límite. Una limpieza imposible de resolver se descarta a mano con `discard_calendar_scrub(tenant_id, event_id, motivo)` (`SECURITY DEFINER`, solo `platform_admin`; motivo obligatorio, recortado a 200 caracteres y sin PII): registra `calendar_scrub_discarded` y deja de listarse. El panel lo expone con `POST /tenants/{tenant_id}/limpiezas-calendar/descartar` del `admin-backend` (cuerpo `{event_id, motivo}`; solo `platform_admin` con `aal2`; 200 `{tenant_id, event_id, estado: "descartada"}`, 404 `limpieza_no_pendiente`, 422 si falta el motivo) y un botón Descartar con motivo obligatorio y confirmación. El job de purga registra, en cada corrida, un reintento fallido con motivo `sin_integracion` para las de `integration_id` nulo (suman intentos); superado el umbral las alerta pidiendo descartarlas a mano y no hace fallar el job por ellas (código de salida 0). Al terminar cada corrida el job emite un log INFO `audit_chain_anchor` por cadena de auditoría (`tenant_id` o null, `chain_seq` y `hash` en base64url, leídos con `audit_chain_heads()`) para anclar las cabezas fuera de la base; si esa lectura falla lo reporta como `audit_chain_anchor_fallido`, termina la purga y sale con código 1. El panel muestra cada fila con su estado (pendiente, reintento fallido, resuelta o descartada). Riesgo aceptado: un calendario que ya no controlamos puede conservar esos datos.

Funciones acompañantes (§2.3): `pseudonymize_channel_user(tenant_id, channel, channel_user_id)` lee la clave de Vault y calcula el HMAC — sin `EXECUTE` para ningún rol de aplicación, solo la usan otras funciones `SECURITY DEFINER`; devuelve `NULL` si Vault no tiene exactamente una clave `channel_user_hmac_key` no vacía (§2.3); `find_dormant_end_customer(channel, channel_user_id)` — solo `bot_service`, exige `app.current_tenant_id`, devuelve la fila dormida más reciente.

`audit_log` deja constancia de que el borrado ocurrió (fecha, quién lo pidió/ejecutó, qué campos) **sin retener el valor original** — evidencia de cumplimiento sin reintroducir el dato personal por la puerta de atrás. El `EXECUTE` de esta función se revoca de `PUBLIC` y se otorga explícitamente solo a `bot_service` y `dashboard_service`. `purge_job` no necesita el grant: la invoca desde dentro de `purge_tenant_data` (una llamada anidada dentro de una función `SECURITY DEFINER` corre con los permisos del dueño), y aun así el actor se registra como `'system'` porque `session_user` sigue siendo `purge_job`. **Tests pgTAP negativos**: `bot_service` con tenant A anonimizando un end_customer de tenant B → falla; `bot_service` sin tenant en contexto → falla; rol no listado → falla; desde `dashboard_service`, `audit_log.actor_type` queda `'staff'`.

### 3.6 MFA
**Obligatorio para `platform_admin` y para `tenant_users` con `is_owner=true`** (Supabase Auth MFA/TOTP). No obligatorio para staff sin rol de owner, para no imponer fricción innecesaria a cuentas de menor privilegio.

**Mecanismo de aplicación** (sin esto, "obligatorio" no es más que una intención): `platform_auth`, al verificar el JWT (§3.4), rechaza cualquier sesión donde `is_owner=true` o `is_platform_admin=true` pero el claim `aal` (Authenticator Assurance Level, expuesto por Supabase Auth) no sea `aal2` — **con una excepción explícita**: una sesión `aal1` sin MFA configurado todavía se deja pasar únicamente hacia el endpoint/flujo de enrollment de MFA, nunca hacia ningún otro endpoint. Sin esta excepción, nadie podría llegar nunca a configurar el MFA la primera vez — el enrollment de Supabase Auth requiere una sesión `aal1` para poder inscribirse, y recién después la sesión sube a `aal2`.

**Defensa propia contra el enrolamiento con una sesión robada**: la excepción de `aal1` no puede servir para agregar un segundo factor a una cuenta que ya tiene MFA (quien robara una sesión `aal1` enrolaría su propio TOTP). Por eso `POST /auth/mfa/enrolar`, en `dashboard-backend` y en `admin-backend`, consulta los factores TOTP verificados del usuario y, si ya hay uno y la sesión no es `aal2`, **rechaza con 403 `mfa_ya_configurada`**; agregar otro factor exige haber pasado antes por `aal2`. No depende de que Supabase Auth lo impida, y **falla cerrada**: si la consulta de factores falla, el enrolamiento se rechaza.

### 3.7 Perímetro y transporte
- **Cloudflare** delante de todo: DNS proxied, WAF managed rules, rate limiting en las rutas de webhook del bot (§2.6) y en `/api/payments/webhook`, Turnstile en logins (WAF/rate limiting sobre los backends; los frontends van solo DNS, §5.3).
- **Webhooks de bot (Telegram/WhatsApp)**: verificación de autenticidad en cada request entrante — `X-Telegram-Bot-Api-Secret-Token` por tenant en Telegram, firma de la app en WhatsApp (provisorio hasta decidir el BSP) — nunca se procesa una actualización sin validar (aplicación directa del principio "cero confianza implícita", §1.7.3). Mecanismo completo, incluida la resolución de tenant, en §2.6. **Idempotencia**: Telegram y WhatsApp pueden reentregar el mismo update si el bot no responde a tiempo — `bot_service` deduplica con `inbound_messages` (§2.10), clave `(tenant_id, channel, external_message_id)` (el `update_id` de Telegram es único solo por bot, por eso la clave incluye el tenant), para que una reentrega no genere un `booking` duplicado si el mensaje reprocesado era justamente una confirmación.
- **Verificación de firma obligatoria** en cualquier webhook de pago; rechazo si no valida.
- **Backups**: el proyecto de producción arranca en el plan Free de Supabase (sin backups) mientras no haya datos reales, y pasa a **Pro con backups diarios (retención 7 días) antes de cargar el primer tenant piloto** — decisión de Franco por costo: el PITR es un add-on de ~USD 100/mes que no se justifica con la base vacía. **Point-in-time recovery queda diferido** hasta que haya tenants que paguen (`PENDIENTESPORDEFINICION.md`). **Límite aceptado**: un backup conserva los datos tal como estaban, incluidos los de end_customers anonimizados después — restaurar los traería de vuelta. Se acepta por la retención acotada de Supabase (días, no años), con un paso obligatorio en el checklist de §6.5 tras cualquier restauración. **Backup externo**: además, un workflow nocturno de GitHub Actions (`.github/workflows/backup.yml`) hace `pg_dump` (sin el esquema `vault`), lo cifra con age y lo sube a un bucket propio de Cloudflare R2 con retención de 30 días (regla de ciclo de vida del bucket); la clave privada solo la tiene el responsable y los secretos de Vault no se recuperan por esta vía. Alta, restauración y secrets: `docs/deploy.md` > "Backup externo".
- **Secrets** (incluidas las credenciales de `tenant_integrations` — bot de Telegram, WhatsApp Business, Google Calendar, etc., §2.11) en Supabase Vault, nunca en una tabla plana ni en variables de entorno versionadas; rotación periódica del `service_role` key.
- **Riesgo aceptado: la clave de la Auth Admin API en el `dashboard-backend`.** El dashboard necesita `SUPABASE_SERVICE_ROLE_KEY` para invitar staff (§2.9), y esa clave no admite recorte: habilita **toda** la Auth Admin API de Supabase. Con ella, quien comprometa el servicio puede cambiar la contraseña de cualquier usuario, borrar sus factores MFA, crear usuarios y generar links de login (magic link / recovery) de cualquier cuenta, **incluido el admin de plataforma**; no toca las tablas de `public` (`service_role` no tiene permisos de tabla, §3.2), pero sí permite suplantar identidades. Se acepta para el piloto (Franco, decisión D2) porque invitar staff es una función central del producto, el servicio es el que más superficie expone a input externo pero valida cada request con JWT y permisos, y el costo de separar la invitación en un servicio propio no se justifica con pocos tenants. **Mitigación:** cada servicio que usa la Auth Admin API (`dashboard-backend`, `admin-backend`, job de purga) lleva **su propia clave secreta de Supabase (`sb_secret_…`)** cargada en la misma variable `SUPABASE_SERVICE_ROLE_KEY` con un valor distinto por servicio (`sync: false` en `render.yaml`), de modo que la del dashboard se revoca y rota sin tocar las otras (procedimiento en `docs/deploy.md`); el bot y el job de vencimiento no llevan ninguna. **Criterio para revisarlo:** antes de pasar del piloto a clientes de pago, ante cualquier sospecha de compromiso del dashboard, o si aparece una alternativa que permita invitar sin la Auth Admin API completa (p. ej. mover la invitación a `admin-backend` o a una función dedicada con una credencial de alcance menor): se reevalúa y, de ser posible, se retira la clave del dashboard.

---

## 4. Base de plataforma (`platform/`, dentro de este proyecto)

**Uso futuro en otros proyectos**: `platform/` se especifica para poder construirse igual en proyectos futuros, sin importar cuáles. Por eso no contiene lógica específica de ningún producto concreto — lo propio de cada producto vive en el resto del repo.

### 4.1 Distribución — dentro del proyecto, sin dependencia externa
`platform/` es un **paquete Python puro** (backend en FastAPI, §5) que vive **dentro** del repo de este proyecto. Cada producto construye su propio `platform/` desde cero, a partir de esta especificación (§2–§4), sin copiar código de otro producto; desde ahí evoluciona solo en este repo. No hay tags ni `pip install` desde git: una mejora descubierta en otro proyecto no llega a este hasta que se aplica a mano. Coherente con §1.7.3 (cero confianza implícita) aplicado también a los propios cambios de Franco, no solo a input externo.

**Frontend, explícitamente sin paquete compartido**: `dashboard-frontend` y `admin-frontend` (Vite+React+TypeScript, §5) se construyen independientes, sin un paquete npm de componentes compartidos — son suficientemente distintos en UI (uno gestiona turnos/calendario, el otro es CRUD+auditoría) como para que compartir sea especular sin evidencia (§1.7.6). Si en algún momento aparece duplicación real, se extrae entonces.

### 4.2 Evolución del schema compartido — copiar una vez, divergir después
*(Esto es distinto de §4.1: §4.1 trata el **código** de `platform/`; esta sección trata las **migraciones SQL**, que también se copian una sola vez.)*
Las migraciones SQL de tenancy/RLS (§2.10, §3) viven en `platform/infra/` como **template**, no como dependencia corrida en vivo. Al crear un producto nuevo, se copian una vez a `supabase/migrations/` de ese repo y desde ahí evolucionan de forma independiente. Si más adelante aparece una mejora (ej. un hardening nuevo descubierto en un proyecto que ya lo usa), se aplica **manualmente** a los productos que ya existen — sin sistema de sincronización automática. **Orden de migraciones**: 1) plataforma (tenancy, roles, `payments` sin ramas de dominio, funciones de §2–§3); 2) dominio turnos (§7); 3) lo que conecta ambos: las ramas del trigger de `payments` hacia `bookings`/`booking_packs` (§2.10) y la limpieza de `payments` en `anonymize_end_customer` (§3.5) — viven en las migraciones del producto, no en el template de `platform/`, que no contiene lógica de producto (§4). Explícitamente no se construye un framework de migración propio: no se justifica el esfuerzo para varios productos con un solo developer part-time (§1.1).

### 4.3 Módulos de `platform/` (Python)

| Paquete | Responsabilidad |
|---|---|
| `platform_tenancy` | Cliente de DB (**asyncpg**, conexión directa a Postgres — no el cliente de Supabase, que va por PostgREST y no permite `set local`, §3.1.3) que exige contexto de tenant en cada query: cada request corre dentro de una **transacción explícita** (`set local` solo vive dentro de ella). Si se conecta vía el pooler de Supabase en modo transacción, va con el cache de prepared statements desactivado (`statement_cache_size=0`), que ese modo no soporta. También setea `app.current_actor_id` (§3.5). Incluye templates de policies RLS (§3.1) y tests pgTAP (§3.3) — estos últimos son SQL, distribuidos por copia según §4.2, no como parte del paquete Python |
| `platform_auth` | Verificación de JWT de Supabase Auth para `tenant_users`, lógica de invitaciones (§2.9), el **único cliente de la Auth Admin API** (`SupabaseAuthAdmin`: invitar y eliminar usuarios con la `service_role`; lo crean al arrancar `dashboard-backend`, `admin-backend` y el job de purga, y lo reciben inyectado), chequeo de `permission_key` (§2.4) en cada endpoint de `dashboard-backend` (§3.1.5) |
| `platform_secrets` | Abstrae Supabase Vault detrás de una interfaz propia (coherente con §1.6, portabilidad) — usado por `tenant_integrations` (§2.6) y cualquier credencial sensible futura |
| `platform_admin` | Lógica backend del panel admin (§1.2, §2.11): endpoints de CRUD de tenants, `platform_settings`, `audit_log`, emisión de sesión de impersonación de solo lectura (rol `platform_admin_readonly`, §3.2). El frontend del admin es un **template copiado una vez** por producto (§5), no un paquete instalado — ver nota de §4.1 |
| `platform_audit` | Helper para escribir `audit_log` de forma consistente desde cualquier servicio |
| `platform_notifications` | Interfaz adapter de bot (`send_message`, `send_buttons`, `on_message`...). Implementaciones: Telegram (dev/test) y WhatsApp Business API (producción, definitiva — §2.6). El proveedor concreto de WhatsApp (Meta Cloud API vs. BSP) queda en `PENDIENTESPORDEFINICION.md` |
| `platform_calendar` | OAuth2 + sync con Google Calendar (usado por los productos que agendan turnos), credenciales guardadas vía `platform_secrets` en `tenant_integrations` |
| `platform_billing` | **Cobro de Franco a sus tenants** (suscripción del SaaS, §1.3). Stub por ahora — se construye recién cuando el modelo de cobro esté decidido. Sin relación con `platform_payments`. |
| `platform_payments` | **Cobro del end_customer al tenant** (señar un turno) — wrapper de Mercado Pago Checkout usando las credenciales propias de cada tenant (`tenant_integrations`, `integration_type='mercadopago'`). Registra cada intento/confirmación en la tabla `payments` (§2.10), genérica entre productos vía `payable_type`/`payable_id`. No depende de que la monetización de §1.3 esté decidida; se construye cuando se integre el cobro de señas/packs de este producto (§7.1) — no en el piloto |

Según el orden de construcción de §1.2: `tenancy`, `auth`, `secrets`, `admin` y `audit` se construyen en el **paso 1** (`platform/` mínimo). `notifications` y `calendar` se construyen recién cuando un producto los necesita. `payments` se construye cuando se integre el cobro de señas/packs (§7.1), no en el piloto. `billing` queda stub hasta que la monetización (§1.3) esté decidida.

---

## 5. Arquitectura de infraestructura

### 5.1 Stack confirmado
- **Frontend**: Vite + React + TypeScript (SPA, sin SSR).
- **Backend**: Python + FastAPI — elegido explícitamente por sobre un stack unificado en TypeScript/Next.js. Python es el lenguaje de mayor fluidez real de Franco (su trabajo diario en Quales/MeLi es BigQuery/Dataflow/Django/FastAPI); prioriza velocidad de desarrollo part-time y menos bugs por desconocimiento del lenguaje, a costa de separar el hosting de frontend y backend (§5.3) en vez de unificarlos en un solo framework/deploy.
- **DB/Auth/Storage/Realtime**: Supabase, un proyecto físicamente separado por producto (§1).

### 5.2 Entornos
- **Dev**: Supabase local (CLI + Docker), corriendo en la máquina de Franco — sin proyecto hosteado para dev, evita pagar/mantener 3 proyectos extra. Los webhooks de Telegram en dev se prueban con un túnel (ej. ngrok) hacia el entorno local.
- **Prod**: un proyecto Supabase hosteado por producto. **Sin staging intermedio** — dev → prod directo, dado que Franco es el único developer y los tests de aislamiento de RLS en CI (§3.3) actúan como red de seguridad antes de cualquier merge a `main`.

### 5.3 Hosting de aplicaciones
- **Por qué estos proveedores**: Vercel, Render y Cloudflare se eligieron por familiaridad de Franco, tier gratuito y simplicidad de deploy.
- **Frontend** (`dashboard-frontend`, `admin-frontend`): Vercel, un proyecto separado por app y por producto. **Plan**: Hobby durante desarrollo y piloto sin cobro; el plan Hobby es solo para uso no comercial, así que pasa a **Pro (USD 20/mes por usuario) antes del primer tenant que pague**. Sus dominios van a Cloudflare **solo como DNS** (sin proxy): Vercel desaconseja poner el proxy de Cloudflare delante (duplica CDN, problemas de cache/certificados). Es aceptable porque un frontend estático no tiene datos — la superficie real son los backends.
- **Backend** (`dashboard-backend`, `admin-backend`, `bot`): Render, servicios separados por app y por producto. **Planes**: `bot` en Starter (USD 7/mes, siempre encendido); `dashboard-backend` y `admin-backend` en Free al arrancar (se duermen tras 15 min sin tráfico y el primer acceso tarda ~1 min — se pasan a Starter cuando arranque el piloto si molesta); Cron Jobs por minuto de ejecución, mínimo USD 1/mes cada uno. **El bot necesita una instancia paga siempre encendida** (y conviene lo mismo para los otros backends): el tier gratuito duerme el servicio tras inactividad y el primer request tarda ~50 s, lo que haría fallar los webhooks.
- **Admin, deploy y URL completamente separados del dashboard del tenant** (§2.11): perímetro de seguridad propio (reglas de Cloudflare más estrictas para `admin.*` que para `app.*`).
- **Cloudflare** con proxy delante de los backends en Render (§3.7): WAF, rate limiting y las reglas más estrictas de `admin.*` se aplican ahí. Turnstile en los logins funciona igual (es un widget, no depende del proxy).

### 5.4 Estructura de repos

```
turnos-saas/                         (raíz del repo)
  platform/                          (Python, construido dentro de este repo, §4.1)
    packages/
      platform_tenancy/  platform_auth/    platform_secrets/
      platform_admin/    platform_audit/   platform_notifications/
      platform_calendar/ platform_payments/ platform_billing/ (stub)
    infra/
      rls-policy-templates/          # SQL, copiado una vez por producto (§4.2)
      pgtap-tests/
    admin-frontend-template/         # Vite+React, copiado una vez por producto (§4.1), no instalado como paquete
  apps/
    dashboard-frontend/              # Vite+React → Vercel
    dashboard-backend/               # FastAPI, usa platform_* → Render
    admin-frontend/                  # copiado de admin-frontend-template → Vercel (deploy separado)
    admin-backend/                   # FastAPI, usa platform_admin → Render (deploy separado)
    bot/                             # FastAPI, webhook Telegram/WhatsApp + webhook de Mercado Pago (platform_payments) → Render
    shared/
      turnos_queries/                # funciones de consulta compartidas por dashboard-backend y admin-backend (§2.11.4)
      turnos_dominio/                # capa de dominio de turnos: cálculo de disponibilidad (huecos puros y calcular_huecos), escritura de turnos (crear_turno y mover_turno) y reglas de `tenants.settings` (settings_tenant); lo usan el bot y el dashboard y no importa ningún canal (§7.1)
      turnos_http/                   # errores HTTP de los backends FastAPI, con una sola forma `{detail: {codigo, mensaje}}` (`error_http`, `no_encontrado`, `respuesta_error`, `http_desde_error_auth`); dashboard-backend y admin-backend lo usan y no importa código de las apps
      turnos_sesion/                 # cookies de sesión (`CookiesSesion`, prefijo `dash`/`adm`) y `MiddlewareSesionCookie` (cookie de acceso a `Bearer` + defensa CSRF por `Origin`) y las dependencias de sesión (`DependenciasSesion`, `dependencias_de`, `sesion_de_app`; cada backend las parametriza con su dependencia de `platform_auth`, su `actor_type` y el contexto de log) y las rutas `/auth/*` como router de módulo (`rutas_auth.router`, parametrizado con un `ServiciosAuth` en `app.state.auth`); dashboard-backend y admin-backend lo usan y no importa código de las apps
      turnos_config/                 # parsers de configuración de arranque (entero, booleano, origen, URL de Supabase, SameSite, header de IP) y `ConfigInvalida`; los `config.py` de dashboard-backend y admin-backend los usan y no importa código de las apps
      turnos_negocio/                # zona horaria del negocio (única, §1.4) y texto del aviso de vencimiento; bot, dashboard y jobs la importan
    jobs/                            # Render Cron Jobs: purga diaria + vencimiento cada 15 min (§5.4.1)
  supabase/
    migrations/                      # copiado de platform/infra una vez (§4.2)
  pyproject.toml                     # incluye platform/ como paquete local (§4.1)
```

**Configuración: se lee y se valida al arrancar.** Cada servicio (bot, `dashboard-backend`, `admin-backend` y los jobs) lee el entorno una sola vez, en su módulo de configuración (`config.py`) o en su punto de entrada (`main.py`, `__main__.py`), y entrega a las operaciones valores ya decididos por parámetro: ninguna operación de plataforma ni handler consulta `os.environ`. Lo que está a medias o es inseguro impide arrancar con un mensaje que nombra la variable (nunca su valor). Google Calendar tiene un estado explícito de «sin configurar»: sin ninguna variable `GOOGLE_OAUTH_*` el servicio arranca con Calendar apagado (en la purga, los eventos quedan como no limpiados por configuración); con algunas pero no todas, o con una clave de `state` demasiado corta, no arranca (`ConfigGoogle.opcional_desde_entorno`). La base de la Bot API de Telegram (`TELEGRAM_API_BASE_URL`, solo `http` hacia una máquina local) se valida igual al arrancar y se inyecta al adapter. Un test estático (`tests/test_configuracion_al_arrancar.py`) falla si un paquete de plataforma o un handler lee el entorno fuera de esos módulos.

### 5.4.1 Jobs programados
Nada en la estructura de arriba ejecuta la anonimización automática de un tenant tras vencer su período de gracia (`platform_settings.tenant_deletion_grace_period_days`, §2.2) — hace falta un **Render Cron Job** por producto (corre el paquete Python `platform_admin` — no confundir con el rol Postgres homónimo —, mismo paquete que el resto del panel admin) que liste diariamente con `list_tenants_due_for_purge()` (§3.2) los tenants `status='inactive'` cuyo período venció **y los que ya están `purging`** (una purga interrumpida se retoma; nunca `'purged'`, §2.10) y dispare la cascada de purga de §2.2, que empieza con `begin_tenant_purge`. Se conecta con el rol `purge_job` (§3.2) y usa `service_role` solo para la Auth Admin API. En la misma corrida borra las filas de `inbound_messages` de más de 7 días (`prune_inbound_messages`, §2.10). Cada falla va a Sentry (§6.1) y la corrida siguiente reintenta; además, si un tenant lleva **más de 3 días** con el período vencido sin llegar a `purged`, el job lo reporta a Sentry aunque no haya habido excepción — detecta una falla silenciosa repetida (tolera una o dos corridas fallidas por algo transitorio). Sin este job explícito, la política de retención de §2.2 queda escrita pero nunca se ejecuta.

**Segundo Cron Job, cada 15 minutos** (`apps/jobs/`, rol `scheduler_job`, §3.2): `expire_pending_bookings()` pasa a `cancelled` los bookings `pending` con `pending_expires_at <= now()` (§7.1) — libera el horario; la sesión del pack la devuelve el trigger de §7.1 — y devuelve `(tenant_id, booking_id, channel, channel_user_id, notify, end_customer_id, starts_at, service_name, outbox_id)` (`starts_at` y `service_name`, inicio y nombre del servicio del turno vencido, para que el aviso diga qué turno venció; `outbox_id` es la fila de `notification_outbox` que la misma sentencia encola para cada `notify` (plantilla `turno_vencido`, primer intento a los 5 minutos), así el aviso sobrevive a que el job muera después de vencer); `notify` es false si el tenant no está `active` (su bot no opera, §2.2) o si el cliente no tiene canal vinculado, y en ese caso `channel`, `channel_user_id` y `end_customer_id` vuelven en `null` (mínimo de dato personal). **`end_customer_id`** va porque el job registra el aviso en `notifications_log` con `record_notification_attempt` (§3.2), que exige el cliente, y `scheduler_job` no tiene `SELECT` sobre ninguna tabla para buscarlo. Con `notify`, el job le avisa al cliente por el bot (§2.6). Como los `pending` nunca van a Google Calendar (§7.1), vencer uno no requiere tocar el calendario; `prune_bot_sessions()` borra las sesiones sin actividad hace más de 1 hora (§2.6). **Reescaneo del outbox** (§6.4): tras avisar los vencidos, cada corrida reclama con `claim_notification_outbox` (`SKIP LOCKED` y arriendo: dos corridas nunca reciben la misma fila; lo que el job dejó a medias vuelve a estar disponible al vencer el arriendo) las filas `pending` cuya espera venció, resuelve el turno y el destinatario con `resolve_outbox_delivery` (solo `scheduler_job`), renderiza el texto con `turnos_negocio.mensaje_aviso_turno` y envía con la misma política de reintentos. Un fallo transitorio con reclamos disponibles deja la fila `pending` para la corrida siguiente; un error permanente del proveedor, la falta de integración o el último reclamo (`OUTBOX_MAX_INTENTOS`, 5) la dejan `failed` (visible en el panel de §2.11.7); sin turno, tenant inactivo o cliente anonimizado se descarta como `failed` sin contar fallo. Lote, arriendo y retención se configuran con `OUTBOX_LOTE` (50), `OUTBOX_ARRIENDO_SEG` (900) y `OUTBOX_RETENCION_DIAS` (30, vía `prune_notification_outbox`). Un horario vencido queda bloqueado como máximo 15 minutos de más. Para no esperar ese margen, los dos canales que escriben turnos liberan al reservar con `release_expired_pending_overlaps(profesional, rango)`: el bot (`bot_service`) y el dashboard (`dashboard_service`, en el alta y en el movimiento de turnos; regla común, decidida por Franco: el alta del dashboard sobre un pendiente vencido ya no responde 409, lo cancela, avisa al cliente por el outbox y crea el turno; un pendiente todavía vigente sigue dando 409 `horario_ocupado`): misma cancelación y misma forma de salida que `expire_pending_bookings`, solo para los vencidos del profesional que solapan con el rango, con contexto de tenant obligatorio y el mismo lock por (tenant, profesional) que las altas; quien la llama encola en el outbox, en la misma transacción de la reserva o del movimiento, el aviso al cliente liberado con las filas devueltas (`turnos_dominio.avisos.encolar_avisos_liberados`; un rollback no deja aviso y, si el proceso muere antes del envío, lo reenvía el reescaneo; el job ya no ve ese turno, así el aviso sale una sola vez). El texto del aviso de vencimiento lo arma `turnos_negocio.mensaje_vencimiento` (día, hora local y servicio del turno, sin datos del cliente), el mismo para el job y para los dos canales. Monitoreo: Sentry por las excepciones (§6.1), la notificación de fallo de Render (§6.2) y el monitor de cron de Sentry `vencimiento-turnos` (horario `VENCIMIENTO_CRON`, `*/15 * * * *`; el mismo helper que la purga, sin `SENTRY_DSN` no hace nada). En WhatsApp, el aviso es un mensaje iniciado por el negocio: requiere plantilla aprobada, atado al BSP pendiente. **Los avisos se envían en paralelo** (`VENCIMIENTO_CONCURRENCIA`, 5 por defecto) y **todo el envío de la corrida está acotado** (`VENCIMIENTO_PLAZO_SEG`, 600 s por defecto, menos que el intervalo: lo que no termine se cancela y cuenta como fallo). Si la credencial del bot de un tenant falla de forma permanente (401/403 o integración inexistente), el primer aviso de ese tenant actúa de sonda: los demás no se intentan, quedan `failed` en `notifications_log` y se reporta un único fallo por tenant.

### 5.4.2 Servicios externos

Datos no secretos de lo ya creado (las credenciales viven en el gestor de contraseñas de Franco, GitHub Secrets o el panel de cada proveedor, nunca acá):

| Servicio | Estado | Datos |
|---|---|---|
| GitHub | creado | Repo privado `francodimartino/turnos-saas`. Protección de `main` cuando existan los checks de CI. |
| Supabase (prod) | creado | Proyecto `turnos-saas-prod`, ID `tpbvxikgynwqvwsrjuyx`, región South America (São Paulo). Plan Free hasta antes del piloto, luego Pro (§3.7). **Data API (PostgREST) desactivada**: nada consulta tablas por HTTP (§3.1.5); Supabase Auth no se ve afectado. |
| Sentry | creado | Organización `nesso-jx`, región de datos EE.UU. (elegida por Franco, sin motivo específico — mitigación en §6.1). Proyectos: `turnos-dashboard-backend`, `turnos-admin-backend`, `turnos-bot`, `turnos-jobs`, `turnos-dashboard-frontend`, `turnos-admin-frontend`. Plan Developer (gratis, sin tarjeta). |
| Render | cuenta lista | Workspace Hobby, GitHub conectado solo a este repo. Servicios y Cron Jobs se crean cuando cada app tenga código (§5.3). |
| Vercel | cuenta lista | Proyectos se crean cuando los frontends tengan código (§5.3). |
| Cloudflare | cuenta lista | Plan Free. DNS, WAF, reglas de `admin.*` y Turnstile pendientes hasta definir el dominio. |
| Dominio | pendiente | Se define antes del primer deploy (`PENDIENTESPORDEFINICION.md`). |
| UptimeRobot | pendiente | Se crea cuando existan los backends (§6.2). |

### 5.4.3 Capas de los backends FastAPI

Un solo patrón para `dashboard-backend`, `admin-backend` y `bot`:

| Capa | Dónde | Qué hace | Qué no hace |
|---|---|---|---|
| Rutas | `rutas_<área>.py` | `router = APIRouter()` a nivel de módulo; valida la entrada, toma pool, deps y config con `Depends` de `request.app.state`, llama al dominio y traduce sus errores a códigos HTTP (`codigo`/`mensaje`) | Reglas de negocio, SQL, endpoints dentro de funciones, fábricas `crear_router_*` |
| Dominio | un módulo por área, sin importar `fastapi` ni `starlette` | Reglas y orquestación (lectura, mezcla, validaciones, auditoría en la misma transacción); recibe la conexión ya abierta; lanza errores propios (`ErrorLocalInexistente`, `ErrorSettingsDemasiadoGrandes`) | Conocer HTTP, `HTTPException`, `Request` |
| Consultas | constantes `SQL_*` en el módulo de dominio o en un módulo de consultas del área | El SQL parametrizado, en un solo lugar | SQL armado en el router |

`platform_admin` (`platform/packages/platform_admin`) es el modelo del dominio: módulos por área (`settings`, `tenants`, `purga`, `notificaciones`…), sin FastAPI, con errores propios (`platform_admin.errores`) y SQL en constantes; `admin-backend` solo traduce HTTP.

**Router de módulo.** `app.py` lo registra con `app.include_router(router)`. Las dependencias que antes llegaban por parámetro de una fábrica se resuelven en cada request desde `app.state` (`app.state.dependencias`, `app.state.config`, `app.state.calendario`) mediante dependencias de módulo (`dependencias.transaccion`, `dependencias.transaccion_con_permiso(clave)`). Un permiso se declara una vez a nivel de módulo (`tx_gestiona_agenda = transaccion_con_permiso("manage_schedule")`) para que las pruebas puedan reemplazarlo. En las pruebas, las piezas falsas se inyectan igual por `app.state` que por `app.dependency_overrides`.

**Ejemplo vigente.** `rutas_settings.py` (dos endpoints, `GET` y `PATCH /local/settings`) más `settings_local.py` (mezcla, tope de tamaño del resultado, auditoría y constantes `SQL_*`). En `admin-backend`, `rutas_panel`, `rutas_visores` y `rutas_impersonacion` ya son routers de módulo: toman la sesión (`sesion_admin`), el pool (`pool_admin`, `pool_readonly`) y el cliente de la Auth Admin API (`cliente_auth_admin_de`) de `app.state`, y el dominio vive en `platform_admin`. En `dashboard-backend` todos los `rutas_*` son routers de módulo: auth (con `invitacion/aceptar`), catálogo, disponibilidad, settings, staff, calendar, clientes y turnos. Estructura de turnos: `rutas_turnos.py` (HTTP; toma la fábrica de Calendar de `app.state.calendario` y las opciones de avisos de `app.state.opciones_avisos`), `gestion_turnos.py` (reglas de alta, movimiento, cambios de estado y packs, con errores `ErrorTurno`), `cierre_turnos.py` (etapa posterior al COMMIT: Calendar, auditoría de su fallo y respuesta), `avisos_turnos.py` (outbox y envíos al cliente), `esquemas_turnos.py` (cuerpos de entrada), `respuestas_turnos.py` (modelos de respuesta) y `turnos.py` (SQL). Cada endpoint declara su modelo con `response_model=`: los de entrada viven en `esquemas_<área>.py`, los de salida en `respuestas_<área>.py` (`<Entidad>Out` y `Lista<Entidades>Out`, campos obligatorios, sin `tenant_id` ni datos internos) y los tipos que conservan la salida de fechas y números en `respuestas_comunes.py`; la lógica sigue devolviendo diccionarios y el modelo valida y filtra lo que sale. Hoy lo declaran turnos, packs, clientes, disponibilidad, catálogo, staff, settings, calendar y las rutas `/auth/*` (estas últimas, compartidas, en `turnos_sesion/respuestas_auth.py`: ningún modelo lleva tokens, que viajan solo en cookies). El alta y el movimiento reintentan la transacción entera ante deadlock o fallo de serialización; el resto del cambio usa una sola transacción por request. Las rutas `/auth/*` de ambos backends salen del router de módulo de `turnos_sesion`; `tests/test_patron_de_capas.py` verifica que ningún `rutas_*.py` use fábricas `crear_router_*` ni endpoints anidados y que los módulos de dominio convertidos no importan FastAPI.

### 5.4.4 Contrato entre backend y frontend

El contrato de cada backend FastAPI con sus frontends es su esquema OpenAPI, versionado en `contratos/dashboard-backend.openapi.json` y `contratos/admin-backend.openapi.json` (JSON con claves ordenadas, sangría de 2 espacios, UTF-8, salto de línea final: la salida es estable byte a byte).

- **Generación.** `python scripts/contrato_openapi.py` arma las dos apps con piezas falsas (pool, Supabase Auth y verificador de prueba: sin red, sin base, sin variables de entorno) y escribe `app.openapi()` en esos archivos; `--verificar` solo compara y sale con código 1 si difieren. El esquema se obtiene en proceso: las apps reales siguen con `/openapi.json`, `/docs` y `/redoc` desactivados, así que no hay nada público.
- **Que no se desactualice.** El esquema cambia con las versiones de FastAPI y Pydantic (p. ej. el detalle de los errores 422): se regenera con el entorno instalado desde `requirements-dev.lock`, el mismo del CI. `tests/test_contrato_openapi.py` regenera el contrato en memoria y falla, con el comando para regenerarlo, si el archivo versionado difiere. Corre en el job de pytest, sin servicios externos. Un cambio de rutas, parámetros o modelos de respuesta se commitea junto con el contrato regenerado.
- **Permisos.** `contratos/permisos.json` exporta el catálogo `platform_auth.Permiso` con `{"claves": [<valores de public.permissions, ordenados>], "miembros": {<NOMBRE>: <clave>}}`; el frontend deriva de ahí sus tipos. Se regenera con `python scripts/contrato_permisos.py` y `tests/test_contrato_permisos.py` falla si queda desactualizado o si aparece una clave de permiso fuera del catálogo. Un test de integración compara el catálogo con la tabla `permissions` de la base migrada.
- **Uso.** Es la fuente para derivar los tipos de los frontends: no se lee de las rutas en ejecución.
- **Tipos del frontend.** Cada frontend deriva los tipos de su API del contrato de su backend con `openapi-typescript` (versión exacta en `devDependencies`). `npm run api:generar` (`scripts/tipos-api.mjs`) escribe el archivo versionado `src/api/generado/contrato.ts` (no se edita a mano ni lo pasa el linter); `npm run api:verificar` solo compara y sale con código 1 si difiere. Los tipos de `src/api/*` son alias de `components["schemas"][...]` (vía `src/api/tipos.ts`: `Esquemas`), sin interfaces escritas a mano ni `as T` sobre las respuestas; `solicitar<T>` toma `T` del contrato y `solicitarSinContenido` cubre los 204. `src/api/contrato.test.ts` falla si el archivo no coincide con el contrato y demuestra con un contrato alterado (campo renombrado) que el código que lo usa deja de compilar. Orden al cambiar una ruta: regenerar el contrato (`python scripts/contrato_openapi.py`) y después los tipos (`npm run api:generar` en el frontend). Lo aplican `dashboard-frontend`, `admin-frontend` y `admin-frontend-template`.
- **Flujo y chequeo en CI.** Cambiar un backend (ruta, parámetro o modelo de respuesta o de permisos) implica regenerar el contrato (`python scripts/contrato_openapi.py`, `python scripts/contrato_permisos.py`), después los tipos de cada frontend afectado (`npm run api:generar`) y commitear los tres. El CI lo exige: el job de pytest corre `contrato_openapi.py --verificar` y `contrato_permisos.py --verificar`, y cada frontend corre `npm run api:verificar`, con un mensaje que dice cómo regenerar; la detección de cambios dispara los frontends si cambia `contratos/` o un backend. `scripts/verificar.py` hace lo mismo en local; como el contrato OpenAPI depende de FastAPI y Pydantic del lock (Python 3.12), con otro Python local el chequeo y su test de pytest se omiten con un aviso (no hay falsos positivos) y lo cubre el CI. `tests/test_verificar_contratos.py` afirma que el workflow y `verificar.py` los invocan.

### 5.5 CI/CD
- **GitHub Actions** (`.github/workflows/ci.yml`) en cada PR: levanta Supabase local, corre los tests pgTAP de aislamiento (§3.3), corre tests Python (pytest) de cada backend, lint/typecheck de TypeScript de cada frontend.
  - **Concurrencia con cancelación**: un push nuevo a la misma PR cancela la corrida anterior (`concurrency` por número de PR con `cancel-in-progress`), para no gastar minutos en commits ya superados.
  - **Jobs por cambios**: un primer job (`cambios`) compara los archivos de la PR con la base y decide si corren los pesados: frontends (si cambió un `*-frontend`, el template o un `package*.json`) y base de datos/Python de integración (`supabase/`, `platform/`, `apps/` salvo frontends, `tests/`, configuración de Python). Los jobs filtrados **siguen existiendo siempre** y terminan en `success`/`skipped` (el filtro va dentro del workflow y no en el trigger), porque un check requerido que nunca se crea quedaría pendiente. `Escaneo de secretos` y `Tests Python (pytest)` corren siempre; una PR solo de documentación corre solo esos.
  - **Sin corrida en push a `main`**: el workflow dispara en `pull_request` y a mano (`workflow_dispatch`). El CI de la PR corre sobre el resultado de mergear con `main` y los merges a `main` no despliegan (deploy manual, abajo), así que repetir la suite completa al mergear solo duplicaba el costo. Para una corrida completa sobre cualquier rama: Actions > CI > Run workflow.
- **Sin branch protection en `main`**: el repo es privado en una cuenta GitHub Free, donde la API de protección de ramas y rulesets no está disponible (decisión de Franco del 2026-10-04, por costo; se reconsidera contratando GitHub Pro, momento en que se aplica la protección con estos mismos checks como requeridos). Mitigación: el único camino a `main` es la PR evaluada con CI verde, con auto-merge del repo desactivado (sin checks requeridos mergearía sin esperar al CI); nunca push directo ni merge manual con checks en rojo. Checks que deben estar en verde antes de mergear: `Escaneo de secretos`, `Tests Python (pytest)`, `Lint y typecheck (frontends) (admin-frontend)`, `Lint y typecheck (frontends) (dashboard-frontend)`, `Tests pgTAP de aislamiento (Supabase local)`, `Tests de integración Python (Supabase local)`. El CI no usa GitHub Secrets hoy (ci.yml no referencia ninguno). La regla "un test de aislamiento roto bloquea el merge" se mantiene, pero se cumple por proceso (solo se mergea con CI verde) y no por GitHub: no hay bloqueo mecánico de la plataforma. Evidencia y decisión completas en [docs/decisiones/2026-10-04-proteccion-main.md](docs/decisiones/2026-10-04-proteccion-main.md).
- **Deploy**: **manual, no automático**: mergear a `main` no despliega (`autoDeploy: false` en `render.yaml`, `git.deploymentEnabled` de `main` en `false` en cada `vercel.json`). El deploy de cada app (por separado: un cambio en `dashboard-backend` no redeploya `admin-backend`) se dispara a mano, detrás de una aprobación humana. Configuración, variables por servicio, roles de conexión y orden del primer deploy: [docs/deploy.md](docs/deploy.md).
- **Secrets de CI** (credenciales de Vercel/Render, tokens de proyecto Supabase) en GitHub Actions secrets, nunca commiteados. Nombres previstos: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SUPABASE_PROJECT_ID`, `VERCEL_TOKEN`, `RENDER_API_KEY` y un deploy hook por app. Hoy `ci.yml` no usa ninguno (los tests corren contra Supabase local); se cargan cuando el deploy los pida.

---

## 6. Observabilidad y operación

### 6.1 Error tracking
**Sentry** integrado en cada backend FastAPI (`dashboard-backend`, `admin-backend`, `bot`), en los **Render Cron Jobs** de purga de tenants y de vencimiento de turnos (§5.4.1 — si no, una falla ahí queda invisible y la retención de §2.2 deja de ejecutarse sin que nadie se entere) y en cada frontend React (`dashboard-frontend`, `admin-frontend`) — captura excepciones no manejadas automáticamente. **Sin datos personales**: los datos de Sentry se guardan en EE.UU., país que Argentina no reconoce con protección adecuada (Ley 25.326), así que todos los SDKs van con `send_default_pii=False` (o su equivalente en el frontend) y un `before_send` que elimina datos de end_customers (teléfono, nombre, `channel_user_id`, cuerpos de mensajes) antes de enviar. **Tampoco credenciales**: el mismo filtro (`apps/shared/observabilidad/pii.py`, `limpiar_secretos`) reemplaza por `[filtrado]` el token de la Bot API de Telegram (va en la URL `/bot<id>:<hash>/`), los valores de `access_token`, `refresh_token`, `id_token`, `client_secret`, `code` y `token` en querystrings o JSON (la vuelta de OAuth de Google trae `code` en la URL) y los `Bearer <token>`; alcanza a los breadcrumbs de `httpx` y a los textos de excepciones, no solo a las claves de PII. Los uuid (ids internos, no son PII) quedan intactos. Una excepción no manejada en cualquier backend la atrapa `MiddlewareHeadersSeguridad` (`apps/shared/seguridad_http`): la manda a Sentry (por el mismo `before_send`) y responde un 500 genérico `error_interno` con los headers de seguridad. Franco se entera de un error antes que el tenant, sin depender de que alguien lo reporte.

### 6.2 Uptime monitoring
**UptimeRobot** (tier gratuito) chequea periódicamente cada servicio backend (por producto) vía un endpoint `GET /health` que devuelve 200 solo si el servicio y su conexión a Supabase están OK. **Alerta por email** a Franco si un chequeo falla. **Los Cron Jobs (§5.4.1) no encajan en este mecanismo** — no es un servicio HTTP que responder a un ping, así que un `/health` no sirve para saber si corrió. Los Cron Jobs de purga diaria y de vencimiento (cada 15 min) usan además el **monitor de cron de Sentry** (detecta que el job no corrió o tardó de más), con las notificaciones de fallo propias de Render Cron Jobs (email al fallar la ejecución) — Sentry (§6.1) cubre las excepciones dentro del job y el email de Render cubre que la ejecución falle.

### 6.3 Logging
Logs estructurados (JSON), incluyendo `tenant_id`/`actor_type` cuando aplique, consultados vía el visor nativo de Render. **Eventos obligatorios** (respaldo de `audit_log` para restauraciones, §6.5): cada `anonymize_end_customer` (con `end_customer_id`, sin PII) y cada paso de la purga de un tenant (§2.2) — sin agregador centralizado adicional por ahora. **Ningún token en logs**: los logs pasan por el mismo filtro que Sentry (§6.1) antes de serializarse, y los loggers de `httpx`/`httpcore` se fijan en `WARNING` porque a nivel INFO escriben la URL completa (con el token del bot de Telegram); un evento obligatorio rechaza campos de PII por construcción (`log_evento_obligatorio`). Se reconsidera si el volumen de logs a la escala de §1.1 lo justifica.

### 6.4 Reintentos y dead-letter de notificaciones
`platform_notifications` reintenta el envío de una notificación fallida hasta **3 veces con backoff exponencial** (1s/5s/25s — valor inicial, sin motivo específico). La fila de `notifications_log` se crea en `'pending'` en el primer intento. Si las 3 fallan, el registro en `notifications_log` (§2.10) queda con `status='failed'` — visible desde el panel admin (§2.11) para que Franco lo detecte y resuelva manualmente (ej. contactar al cliente por otro medio). Sin esto, un fallo transitorio de red podría hacer que un cliente se pierda la confirmación de su turno sin que nadie se entere. **Qué se reintenta**: red, timeouts, 5xx y 429. Un 4xx distinto de 429 es permanente (ej. 403 porque el usuario bloqueó al bot): la fila pasa a `failed` en el primer intento, sin reintentos. Ante un 429 se espera el `retry_after` que indica Telegram (en lugar del backoff fijo), acotado a 60 s por defecto (`tope_retry_after`) para no dormir el background task; cada 429 consume uno de los 3 reintentos. Un timeout se reintenta aunque Telegram pudiera haber recibido el mensaje: se prefiere un posible duplicado a perder el aviso.

**Ejecución sin bloquear el request**: los reintentos corren como `BackgroundTasks` de FastAPI (proceso en background dentro del mismo servicio, sin cola externa) — no dentro del ciclo de request/response que los dispara. Sin esto, el webhook que originó el envío (ej. confirmar un booking) quedaría esperando ~30 segundos (1s+5s+25s de backoff) antes de poder responder. No se introduce una cola de tareas externa (Celery/Redis) porque no se justifica para el volumen de §1.1 — si el volumen de reintentos creciera, se reconsidera. **Límite conocido y aceptado**: `BackgroundTasks` vive en memoria del proceso — si Render redeploya a mitad de una secuencia de reintentos, ese reintento se pierde sin llegar a `status='failed'` y queda en `'pending'` — el visor de §2.11.7 lo muestra pasada 1 hora. Riesgo aceptado dado el volumen y la frecuencia de deploy esperados (§1.1); si se vuelve un problema real, se resuelve con una cola persistente. **Outbox**: los avisos de turno del bot y del dashboard (incluido el de vencimiento al cliente cuyo pendiente vencido se libera al reservar, que encola `turnos_dominio.avisos.encolar_avisos_liberados`) se encolan en `notification_outbox` dentro de la misma transacción del cambio (sin commit no hay aviso); tras el COMMIT el envío inmediato sigue siendo una `BackgroundTask` y cierra la fila (`sent`/`failed`). Si el proceso muere antes, la fila queda `pending` y el reescaneo del job de vencimiento la envía; el límite anterior de redeploy aplica solo a los envíos que no son avisos de turno (las respuestas conversacionales del bot). Ningún aviso de turno se envía por fuera del outbox. **Otro límite conocido, de menor severidad**: si un envío se marca "fallido" por timeout de nuestro lado pero en realidad sí llegó, el reintento le manda al cliente el mismo mensaje duplicado — molesto (ej. la confirmación de un turno llega dos veces), no peligroso como el caso de bookings/orders duplicados de la reentrega de webhooks (§3.7). Se acepta sin agregar idempotency keys del lado de envío, mismo criterio de no sobre-diseñar para el volumen de §1.1.

### 6.5 Checklist de respuesta a incidentes (mínimo, no un proceso formal)
Ante sospecha de fuga de datos entre tenants o cualquier incidente de seguridad:
1. Suspender el tenant afectado (`tenants.status='suspended'`, §2.2) si corresponde, para contener el impacto — no arranca el reloj de purga y se revierte desde el panel admin.
2. Correr los tests de aislamiento pgTAP (§3.3) contra el proyecto de producción afectado, para confirmar si hay una policy rota (pgTAP corre dentro de una transacción con rollback automático — no deja datos de prueba en producción).
3. Revisar `audit_log` del tenant/período en cuestión para reconstruir qué pasó.
4. Si se confirma exposición real de datos de un tenant a otro, notificar a los tenants afectados.
5. Aplicar el fix, agregar un test pgTAP que cubra específicamente el caso encontrado (para que CI lo detecte si vuelve a romperse), y dejar un registro breve del incidente (fecha, causa, fix).

**Si hay que restaurar un backup (diario o PITR, §3.7)** — por un incidente o por cualquier otra causa: **antes** de restaurar, exportar de la base actual la lista de `anonymize_end_customer` registradas en `audit_log` posteriores al punto de restauración (`audit_log` vive en la misma base y también vuelve atrás); si la base actual no es accesible, sacarla de los logs estructurados (§6.3), donde cada anonimización queda registrada con el `end_customer_id`, sin PII. **Después** de restaurar, volver a aplicarlas. **Lo mismo con las purgas de tenant**: un tenant purgado después del punto de restauración volvería como `inactive`, con sus credenciales de Vault y cuentas Auth ya eliminadas — se vuelve a correr su cascada (§2.2), que es idempotente. Sin este paso, restaurar devolvería datos que las personas pidieron borrar.

---

## 7. Modelo de datos del dominio (turnos)

**Nota de integridad**: todos los `status`/`type`/`origin`/`payment_status` de esta sección llevan también su `check (columna in (...))` per §1.7.7 — no se repite en cada tabla, pero aplica igual.

**Nota de RLS, principio general del documento (no exclusivo de esta sección — ej. `tenant_user_permissions` en §2.10 sigue el mismo criterio)**: toda tabla lleva `tenant_id` **directo**, incluso las que cuelgan de otra tabla del mismo dominio (ej. `availability_rules` de `professionals`) — es redundante con la FK al padre, pero evita que la policy de RLS de una tabla hija necesite un `EXISTS`/subquery contra la tabla padre para saber de qué tenant es. Una policy con join es más lenta y, sobre todo, más fácil de romper sin que se note — exactamente lo que §3.1 busca evitar. **El valor lo fuerza siempre un trigger `BEFORE INSERT`/`UPDATE`** que copia `tenant_id` desde la fila padre (`professionals`, etc.), **ignorando cualquier valor que mande la aplicación** — nunca se confía en que el código de la app lo setee bien, porque si pudiera hacerlo (y tuviera un bug), RLS estaría validando una columna que no refleja la realidad y el aislamiento se rompería en silencio. Mismo principio de §1.7.3 (cero confianza implícita), aplicado también al propio backend, no solo al input externo. **Tablas con varios padres** (`bookings` → `end_customers`/`services`/`professionals`; `professional_services`, `booking_packs`, `availability_blocks`, `notifications_log`, etc.): el trigger copia `tenant_id` de uno solo, así que eso no impide que un booking de A apunte a un servicio de B. Por eso toda tabla con `tenant_id`, `id` propio y referenciada por otras lleva además `unique (tenant_id, id)`, y **toda FK hacia otra tabla del tenant es compuesta**: `foreign key (tenant_id, service_id) references services (tenant_id, id)`. Con el `tenant_id` forzado por trigger y FKs compuestas hacia todos los padres, una referencia cruzada entre tenants es físicamente imposible (test pgTAP negativo, §3.3). Una FK con columna nullable (ej. `availability_blocks.professional_id`) no se chequea cuando es null, que es el comportamiento correcto.

### 7.1 Turnos (estética/peluquería)

**Multi-profesional**: un tenant tiene uno o más `professionals` (empleados), cada uno con su propia disponibilidad y agenda — no hay agenda única de negocio, todo booking se asigna a un profesional específico.

```sql
professionals (
  id uuid pk, tenant_id fk, name, is_active boolean default true, created_at,
  tenant_user_id uuid null unique fk,  -- opcional: la cuenta del profesional, para que apruebe sus turnos (modo 'approval'); solo el owner lo vincula, re-vincula o desvincula (§2.9)
  -- al pasar is_active=false, se elimina de Vault el secret de su tenant_integrations
  -- de google_calendar (si tenía una conectada) — si no, el sync seguiría empujando
  -- eventos al calendario personal de alguien que ya no trabaja en el tenant
)

availability_rules (
  id uuid pk, tenant_id fk, professional_id fk,
  day_of_week int,        -- 0=domingo … 6=sábado
  start_time time, end_time time,
  created_at,
  check (end_time > start_time),   -- una ventana invertida rompería el cálculo de huecos disponibles
  check (day_of_week between 0 and 6)
)

availability_blocks (   -- excepciones: feriados, vacaciones, ausencias puntuales
  id uuid pk, tenant_id fk,
  professional_id uuid null fk,   -- null = bloquea todo el local
  time_range tstzrange not null,
  reason text, created_at,
  check (not isempty(time_range))
)

professional_services (   -- qué servicios hace cada profesional y cómo se confirman
  tenant_id fk, professional_id fk, service_id fk,
  confirmation_mode text not null default 'auto',   -- 'auto' | 'approval' | 'deposit'
  primary key (professional_id, service_id)
)

services (
  id uuid pk, tenant_id fk, name,
  duration_minutes int,   -- configurable por servicio, define el tamaño del hueco a buscar
  price numeric,
  deposit_amount numeric,  -- seña del servicio; obligatoria si algún professional_services lo usa en modo 'deposit' (trigger). Sin cobro real en el piloto, ver más abajo
  is_active boolean default true, created_at,
  check (duration_minutes > 0),   -- un 0/negativo generaría un time_range vacío y el exclusion constraint de bookings dejaría de prevenir el doble-booking
  check (price >= 0), check (deposit_amount is null or deposit_amount >= 0)
)

bookings (
  id uuid pk, tenant_id fk, end_customer_id fk, service_id fk, professional_id fk not null,
  time_range tstzrange not null,   -- derivado de start_at + duration_minutes del servicio
  status text not null default 'pending',             -- 'pending' | 'confirmed' | 'cancelled' | 'completed' — not null: un NULL escaparía al exclude de abajo (§1.7.7)
  deposit_status text not null default 'not_required', -- 'not_required' | 'pending' | 'paid'
  booking_pack_id uuid null,   -- si se pagó con una sesión de un pack en vez de seña/dinero suelto; al pasar a 'cancelled', la sesión la devuelve UN ÚNICO trigger AFTER UPDATE de bookings (used_sessions -= 1, fila por fila) — ninguna función ni backend la devuelve por su cuenta, así no se devuelve dos veces
  pending_expires_at timestamptz null,   -- solo en 'pending': menor entre now() + booking_approval_timeout_hours y el inicio del turno
  google_calendar_event_id text,
  created_at,
  exclude using gist (professional_id with =, time_range with &&) where (status <> 'cancelled'),
  check ((status = 'pending') = (pending_expires_at is not null))
)

booking_packs (
  id uuid pk, tenant_id fk, end_customer_id fk, service_id fk,
  total_sessions int, used_sessions int default 0,
  origin text not null,     -- 'purchase' (pago real) | 'manual_grant' (asignado por el tenant)
  payment_status text not null default 'not_required',  -- 'not_required' (manual_grant) | 'pending' | 'paid' (purchase, vía payments) — sin esto, un pack comprado y no pagado podría redimirse igual
  expires_at timestamptz, created_at,
  check (used_sessions <= total_sessions),   -- evita sobre-redimir el pack por un bug o una race condition
  check (total_sessions > 0), check (used_sessions >= 0),
  check (
    (origin = 'manual_grant' and payment_status = 'not_required')
    or (origin = 'purchase' and payment_status in ('pending','paid'))
  )   -- bidireccional: evita tanto "regalado pero pendiente de pago" como "comprado pero marcado como no-requiere-pago" (esto último se colaba con un check solo de un lado)
)
```

**Disponibilidad — cálculo dinámico, no slots cargados a mano**: el tenant carga `availability_rules` recurrentes (ej. "lunes a viernes 9-18hs") por profesional. Al pedir un turno de un servicio, el sistema calcula los huecos libres restando los `bookings` existentes de ese profesional y sus `availability_blocks` (propios y los de todo el local) a sus `availability_rules`, usando `duration_minutes` del servicio elegido — nadie carga turnos disponibles manualmente.

**Capa de dominio (`apps/shared/turnos_dominio`)**: el cálculo de huecos vive en un paquete compartido, no dentro del bot. `huecos` es lógica pura (reglas semanales - ocupados -> huecos, sin base ni framework) y `consulta.calcular_huecos(conn, profesional, servicio, rango)` lee la base dentro de `tenant_tx`. La zona horaria sale solo de `turnos_negocio`. Lo usa el bot (`bot_app.reserva`) y lo pueden usar los demás canales (panel del dueño, jobs) sin duplicar el cálculo. El dominio no importa `fastapi`, `bot_app`, `dashboard_backend` ni `admin_backend` (lo afirma un test estático): calcula, y cada canal decide qué reglas aplica sobre el resultado (por ejemplo, el bot ofrece solo horarios futuros).

**Alta y movimiento de turnos en el dominio** (`turnos_dominio.alta`): `crear_turno(conn, ...)` hace un solo INSERT de `bookings` y `mover_turno(conn, ...)` un solo UPDATE de `time_range`. Ambos corren dentro de un savepoint, toman el lock por (tenant, profesional) y traducen el choque (`23P01`, y `40P01`/`40001` residuales) a un resultado: `None` al crear, `"choque"` al mover (`"movido"` y `"no_existe"` completan los casos). Un error distinto se propaga tras volver al savepoint. La transacción la abre el canal (`tenant_tx`); el dominio no abre ni cierra nada y no encola avisos: el canal encola el outbox en la misma transacción cuando el alta devuelve el turno. Lo que decide cada canal entra como parámetro explícito: `estado` (`confirmed` sin vencimiento o `pending` con `pending_expires_at`), `booking_pack_id` (con él consume, bajo el mismo lock y antes del insert, una sesión del pack; si no hay, levanta `PackNoDisponible` sin insertar y un rollback al savepoint devuelve la sesión si el insert falla), y dos reglas opcionales que el canal activa: `limites` (`LimitesReserva`: anticipación mínima, tope de turnos futuros activos y reservas por hora, revalidados bajo el lock por cliente y el del profesional; un límite excedido levanta `ReservaRechazada` sin insertar) y `liberar_vencidos` (cancela los `pending` vencidos que solapan; la liberación es `liberar_vencidos_solapados`, y los `TurnoCreado.liberados` vuelven para que el canal encole su aviso en el outbox, en la misma transacción, con `turnos_dominio.avisos.encolar_avisos_liberados`, y lo envíe tras el commit con `pedido_de_envio`). Orden fijo: savepoint, lock del cliente (solo con `limites`), lock del profesional, consumo del pack, límites, liberación, escritura. Un canal que reintenta su transacción completa ante un deadlock pasa `concurrencia_como_choque=False` y recibe la excepción en vez del resultado de choque. Lo usan los dos canales con las mismas funciones: el bot (`bot_app.reserva.repositorio.crear_booking`, con `limites` y `liberar_vencidos`) y el dashboard (`POST /turnos` y `/turnos/{id}/mover`: el alta nace `confirmed`, sin vencimiento, sin límites y sin exigir disponibilidad, porque el negocio puede forzar un horario fuera de atención; igual que el bot, libera los pendientes vencidos que ocupan el horario: el alta pasa `liberar_vencidos=True` y el movimiento llama a `liberar_vencidos_solapados` bajo el lock del profesional, ambos con el aviso de vencimiento encolado en la misma transacción y enviado tras el commit con `CompletadorOutboxFuncion`; el choque con un pendiente todavía vigente o con un turno confirmado sigue siendo 409 `horario_ocupado`). Ningún otro código fuera de `supabase/` inserta en `bookings` ni cambia su `time_range` (lo afirma un test estático).

**Prevención de doble-booking a nivel de base de datos**: el `exclude using gist` sobre `(professional_id, time_range)` hace que Postgres **rechace físicamente** un booking que se superponga con otro del mismo profesional, incluso ante una condición de carrera de dos requests simultáneos — coherente con el principio de no confiar solo en la capa de aplicación (§1.7.2). Requiere la extensión `btree_gist` (`create extension btree_gist;`) porque `professional_id` es `uuid`, no un tipo de rango — sin la extensión, Postgres no puede indexar la igualdad de `uuid` dentro de un índice GiST.

**Transiciones de estado de `bookings`** (las hace cumplir el trigger `bookings_transiciones_estado`, migración `20260102000150_bookings_transiciones.sql`, no la aplicación): `pending → confirmed`, `pending → cancelled`, `confirmed → completed`, `confirmed → cancelled`, y mantener el mismo estado. **`cancelled` y `completed` son finales** (cualquier otra transición se rechaza con `23514`). Al salir de `pending` el mismo trigger deja `pending_expires_at` en `null` (lo exige el check de la tabla, y así ningún backend tiene que acordarse). **Por qué finales**: con `cancelled` irreversible, la sesión del pack la devuelve el trigger de arriba una sola vez; si un turno cancelado pudiera "revivir", se devolvería dos veces o se consumiría sin control.

**Lock por profesional compartido por bot y dashboard**: toda alta o movimiento de un turno toma primero `pg_advisory_xact_lock` con una clave por `(tenant, profesional)` (`apps/shared/turnos_queries/bloqueo.py`), dentro de la misma transacción y antes de tocar cualquier otra fila (pack, turno nuevo): un solo orden, sin ciclos. La clave vive en un módulo compartido y la usan idénticamente `bot` (`reserva/repositorio.py`) y `dashboard-backend` (`rutas_turnos.py`, alta y movimiento), porque si cada uno armara la suya no se serializarían entre sí. **Por qué**: sin el lock, dos transacciones que insertan turnos solapados chocan en el exclusion constraint, se esperan mutuamente al validar el índice y Postgres aborta una con deadlock (`40P01`) de forma intermitente; con el lock, la segunda espera, ve la fila de la primera y recibe el `23P01` limpio (turno ocupado). El exclusion constraint sigue siendo el límite real; el lock solo evita el deadlock y no frena a otros profesionales.

**Límites de reserva del bot (anti-abuso)**: el bot rechaza una reserva si el cliente ya tiene el máximo de turnos futuros `pending`/`confirmed` (`booking_max_active_per_customer`, default **5**), si ya creó el máximo de turnos en la última hora (`booking_max_per_hour`, default **3**) o si el turno empieza antes de la anticipación mínima (`booking_min_notice_minutes`, default **30**). Cada tenant puede cambiarlos en `tenants.settings` (rangos 1-50, 1-60 y 0-1440, validados por trigger en la base, §2.10); si la clave falta o no es válida, el bot aplica el default (no se guarda). Se revalidan **bajo los locks del cliente y del profesional, dentro de la transacción que crea el turno** (no solo al mostrar los horarios), para que dos pedidos simultáneos no los salteen. **Rigen solo para el bot**: los turnos que crea el staff desde el dashboard están exentos.

**Cancelación/reprogramación — PROVISORIO, política pendiente (ver `PENDIENTESPORDEFINICION.md`)**: en el piloto, el bot **no** cancela ni reprograma — si el end_customer lo pide, le indica que contacte al negocio. El tenant cancela o mueve el turno desde el dashboard (mover = `dashboard-backend` actualiza `time_range` del mismo booking). Cuando se decida la política, se definen la anticipación mínima, su valor por defecto y la regla fuera de ventana.

**Modo de confirmación — por profesional y por servicio** (`professional_services.confirmation_mode`): el bot solo ofrece, para el servicio elegido, los profesionales activos que lo hacen. Al reservar: `'auto'` → el booking nace `confirmed`; `'approval'` → nace `pending` con `pending_expires_at` = el menor entre `now()` + `tenants.settings->>'booking_approval_timeout_hours'` (default 24; entero entre 1 y 168, validado por trigger en la base, §2.10) y el inicio del turno — nunca queda pendiente pasado su horario. Lo aprueba el profesional (vía `professionals.tenant_user_id`) o cualquier `tenant_user` con `approve_bookings` (el owner siempre); si nadie lo aprueba, el Cron Job de §5.4.1 lo cancela, libera el horario y avisa al cliente. **Por qué existe `'approval'`**: en el piloto funciona como doble paso de prueba; en producción cada profesional decide por servicio si lo salta (decisión de Franco). `'deposit'` → `pending` hasta pagar la seña; **no se puede elegir en el piloto** (el dashboard no lo ofrece hasta que se integre el cobro), pero el valor existe para no requerir migración.

**Señas**: `deposit_amount`/`deposit_status` existen en el modelo desde el piloto, pero **sin cobro real integrado todavía** — el booking se confirma sin depender de un pago efectivo. Cuando se integre (señas y `booking_packs.origin='purchase'`), es vía `platform_payments` (§4.3) — no bloquea el piloto de turnos, se suma en una iteración posterior.

**Google Calendar — un calendar por profesional, sync unidireccional**: cada `professional` conecta su propio Google Calendar (OAuth2, credenciales en `tenant_integrations` con `professional_id` seteado — ver ajuste de schema en §2.10). El sistema (Postgres) es la única fuente de verdad: **el evento se crea recién cuando el booking pasa a `confirmed`** (un `pending` nunca va al calendario), se actualiza al moverlo y se borra al cancelarlo — lo hace el backend que produjo el cambio (`bot` o `dashboard-backend`), obteniendo la integración del profesional con `find_integration('google_calendar', professional_id)` (§2.6); pero un cambio hecho directamente en Google Calendar **no se refleja de vuelta** — evita construir manejo de webhooks de Calendar y resolución de conflictos para el piloto. Si el tenant quiere mover un turno, lo hace desde el dashboard, no desde Calendar. **Contenido del evento**: servicio + nombre y teléfono del cliente — para dar más accesibilidad al negocio (decisión de Franco). Por eso la anonimización edita todos los eventos de ese cliente, pasados y futuros (§3.5); si no puede, queda registrado y visible para Franco (riesgo aceptado).

**Packs de turnos**: soportan ambos orígenes confirmados — comprados con pago real (`origin='purchase'`, depende de la misma integración de cobro que las señas) o asignados manualmente por el tenant desde el dashboard (`origin='manual_grant'`, sin pago, ej. programa de fidelidad). La lógica de sesiones usadas/vencimiento es igual para ambos; solo cambia el origen registrado. **Un pack solo puede redimirse (usarse para pagar un booking) con `origin='manual_grant' OR payment_status='paid'`** — nunca un `'purchase'` en `'pending'` — sin esto un pack comprado y no pagado todavía daría sesiones gratis.

## 8. Riesgos abiertos y decisiones diferidas
Ver [`PENDIENTESPORDEFINICION.md`](PENDIENTESPORDEFINICION.md) — registro vivo de todo lo que queda abierto a propósito (canal WhatsApp, monetización, secciones sin cerrar todavía). Se revisa periódicamente y cada ítem se mueve a este documento cuando se cierra.
