# Pendientes por definición

> Registro vivo de decisiones de arquitectura todavía abiertas o diferidas a propósito. Revisar periódicamente — cuando una se cierre, se mueve a `ARCHITECTURE.md` con su decisión final y se borra de acá.

## Decisiones diferidas a propósito (no bloquean el trabajo actual)

- **Triple fila si el tenant recarga manualmente a alguien ya anonimizado antes de que esa persona le responda al bot** — si una persona pide borrar sus datos, el tenant la vuelve a cargar manualmente sin saber que ya existía (el teléfono de la fila vieja quedó en `null` y su `channel_user_id` reemplazado por un hash tras anonimizar, `ARCHITECTURE.md` §2.3), y esa persona después le escribe al bot y elige "empezar de cero" (§2.3), se crea una tercera fila en vez de fusionarse con la que el tenant ya cargó. Edge case raro, aceptado sin resolver por ahora — si se vuelve un problema real, se define un mecanismo de fusión/alerta en ese momento.

- **Impersonación con escritura en el panel admin** — el MVP del panel admin (`ARCHITECTURE.md` §2.11) solo permite impersonar en modo lectura; si en algún momento hace falta poder operar el dashboard de un tenant como soporte activo (no solo diagnosticar), se define ahí el mecanismo y el audit trail reforzado que necesitaría.

- **Panel admin unificado cross-producto** — se eligió un panel admin por producto (§2.11) en vez de uno solo que muestre los productos juntos. Si en algún momento la fricción de tener 3 logins separados pesa más que el aislamiento, se reconsidera.

- **Soporte a sucursales (multi-location por tenant)** — por ahora un tenant = un local físico. Si un cliente tuyo aparece con varias sucursales, se define en ese momento si conviene modelar una entidad `locations` bajo el tenant (con su propio alcance de RLS) o si sigue siendo un tenant por sucursal. Contexto: `ARCHITECTURE.md` §2.5.

- **Proveedor de WhatsApp Business API (BSP)** — confirmado que el canal final de producción es WhatsApp SIEMPRE (Telegram es solo dev/test, nunca producción) y que cada tenant tiene su propio número verificado (mismo patrón que Telegram, `ARCHITECTURE.md` §2.6). Falta decidir el proveedor concreto: Meta Cloud API directo, o un BSP como Twilio/360dialog. También sigue sin decidir si la lógica del bot en WhatsApp es determinística (igual que Telegram) o un bot agéntico con Claude vía MCP (Model Context Protocol). La verificación de firma del webhook de WhatsApp quedó marcada provisoria hasta esta decisión (el patrón "verificar firma → resolver tenant por `phone_number_id`" ya está fijo). Contexto: `ARCHITECTURE.md` §2.6, §4 (`platform_notifications`).

- **Modelo de monetización de Franco hacia sus tenants** — no decidido si el cobro de la suscripción del SaaS es gestionado por el sistema (integración de pagos, `platform_billing` "hacia arriba") o manual/fuera del sistema (factura o transferencia aparte), y puede variar por producto. El esquema (`tenants.plan`/`tenants.status`) ya quedó diseñado para soportar cualquiera de las dos sin romperse. Contexto: `ARCHITECTURE.md` §1.3.

- **Recordatorios automáticos antes del turno** (ej. 24 h antes) — diferidos a propósito: en WhatsApp, un mensaje iniciado por el negocio requiere plantillas aprobadas por Meta y depende del BSP (arriba). El piloto solo manda la confirmación al reservar y los avisos de aprobación/vencimiento. Contexto: `ARCHITECTURE.md` §2.6.

- **Modo de confirmación 'deposit' (con seña)** — existe en el modelo (`professional_services.confirmation_mode`) pero no se puede elegir hasta integrar el cobro con `platform_payments`. Contexto: `ARCHITECTURE.md` §7.1.

- **Webhook de Mercado Pago: resolución de tenant y verificación de firma** — mismo problema que el webhook del bot (previo al contexto de tenant, credencial por tenant), sin resolver porque el cobro no entra en el piloto. Cuando se integre `platform_payments`, se define con el mismo patrón de `ARCHITECTURE.md` §2.6 (identificador no secreto por tenant → función `SECURITY DEFINER` → verificación con la credencial del tenant). Contexto: §3.2, §3.7, §4.3.

- **Dashboard de métricas de negocio** (ej. tenants activos, turnos/ventas de la semana, tasa de notificaciones fallidas) — no entró en el MVP del panel admin (§2.11) ni en observabilidad (§6). Si hace falta, se define cuando surja la necesidad concreta.

## Secciones del SDD todavía sin ronda de definición

- **Política de cancelación/reprogramación de turnos vía bot** — se define más adelante por Franco. Mientras tanto, en el piloto solo el negocio cancela/mueve turnos desde el dashboard; el bot deriva al negocio (`ARCHITECTURE.md` §7.1, marcado provisorio).
