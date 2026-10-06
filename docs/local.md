# Probar la app completa en tu computadora

Esta guía levanta **todo el producto en tu máquina** (base de datos, los tres servicios, las dos pantallas web, los
procesos automáticos y un "Telegram de mentira") con **un solo comando**, para probarlo antes de publicarlo. No toca
producción, no usa cuentas reales y todo lo que se crea es de prueba: se puede borrar y empezar de cero cuando quieras.

## Qué necesitás

1. **Windows con Docker Desktop abierto** (el ícono de la ballena en la barra, sin errores). Es lo que corre todo.
2. **Node.js** instalado (trae el comando `npx`, que se usa para la base de datos local).
3. **Python 3.10 o más nuevo** (solo para lanzar el comando; los servicios corren adentro de Docker y no usan el
   Python de tu máquina).
4. Unos **5 GB libres** y buena conexión la **primera vez** (baja imágenes y paquetes: tarda varios minutos). Las
   siguientes veces arranca en un par de minutos.

Los puertos que usa son propios de este proyecto, así que **no molesta a otros proyectos Supabase** que tengas
corriendo: 55420 a 55424 (base de datos y Supabase), 8110 a 8112 (servicios), 8120 (simulador de Telegram) y 5173 y 5174
(pantallas web). Si alguno está ocupado, el comando avisa con un error.

## Levantar todo

Desde la carpeta del proyecto, en una terminal:

```
python scripts/local.py up
```

Al terminar imprime las direcciones y los usuarios. Es **idempotente**: si ya estaba levantado, lo deja igual, y los datos
de demo no se duplican. Todo lo generado (contraseñas de prueba de la base, claves) queda en `.env.local`, un archivo que
**no se sube al repositorio**.

### Direcciones

| Qué | Dirección |
|---|---|
| Dashboard del negocio (dueña y equipo) | http://localhost:5173 |
| Panel de administración (Franco) | http://localhost:5174 |
| Simulador de Telegram | http://localhost:8120 |
| Correos que manda el sistema (invitaciones) | http://localhost:55424 |
| Base de datos (Supabase Studio) | http://localhost:55423 |

### Usuarios de demo (solo existen en tu máquina)

Todos entran con la misma contraseña de demo: se genera al azar la primera vez, **la imprime `up` al final** y queda guardada en
`.env.local` (variable `DEMO_PASSWORD`). Solo vale en tu máquina.

| Quién | Email | Qué puede hacer |
|---|---|---|
| Franco, administrador de la plataforma | `franco@turnos.test` | Panel de administración (5174) |
| Dueña del negocio "Estética Demo" | `duena@turnos.test` | Todo en el dashboard (5173) |
| Recepción, equipo con permisos limitados | `recepcion@turnos.test` | Dashboard (5173): solo ver turnos y aprobarlos |

El negocio de prueba **Estética Demo** trae: 3 servicios (Corte de pelo, Coloración y Manicura; la **Coloración** requiere
aprobación del local), 2 profesionales (Lucía y Mariano), horarios de lunes a sábado, un bloqueo de agenda (mañana de
14 a 16, Lucía) y 3 clientes (Ana, Bruno y Carolina). Su bot de Telegram ya está conectado al simulador.

### Verificación en dos pasos (MFA): la primera vez

Franco y la dueña **tienen que activar la verificación en dos pasos** (así lo exige el producto). La primera vez que
entran, la pantalla muestra un código QR:

1. Instalá en el celular una app de códigos (Google Authenticator, Microsoft Authenticator, Authy, 1Password…).
2. En la app, "agregar cuenta" y escaneá el QR de la pantalla.
3. Escribí en la pantalla el código de 6 dígitos que muestra la app. Listo: desde entonces, cada ingreso pide el código.

Si perdés la app: `python scripts/local.py reset` (borra todo y arranca de cero), o en Studio
(http://localhost:55423) > Authentication > Users > la cuenta > borrar el factor. El reloj de la computadora tiene que
estar en hora, si no los códigos no coinciden. La recepción (equipo) no usa MFA.

## Recorrido paso a paso: qué probar

### 1. Panel de administración: crear un negocio
1. Entrá a http://localhost:5174 con Franco y activá la verificación en dos pasos.
2. **Tenants > Nuevo**: poné nombre, un identificador corto (ej. `peluqueria-sol`), el email de la dueña (ej.
   `sol@turnos.test`) y su nombre.
3. El sistema "envía" la invitación: mirala en http://localhost:55424 (es un buzón de prueba, no sale ningún correo). El
   link abre el dashboard para elegir contraseña. Si preferís saltearlo: `python scripts/local.py clave sol@turnos.test`
   le deja la contraseña de demo.
4. En la ficha del negocio, sección **Telegram**, cargá una integración: un token cualquiera (ej. `123456:prueba`) y un
   secret cualquiera. Anotá la **clave del webhook** (`webhook_key`) que muestra.
5. En el simulador (http://localhost:8120) tocá **+ Bot** y cargá el nombre, el mismo token, la clave del webhook y el
   mismo secret: desde ahí podés "chatear" con el bot de ese negocio.

### 2. Dashboard del negocio
Entrá a http://localhost:5173 con `duena@turnos.test` (y activá MFA). Probá, en las pestañas de arriba:
- **Servicios**, **Profesionales** y **Disponibilidad** (catálogo y agenda): crear, editar, desactivar; agregar un
  bloqueo.
- **Datos del local**: dirección, horarios y contacto (es lo que el bot responde en "Datos del local").
- **Clientes**: ver la lista (los 3 de demo más los que lleguen por el bot), editarlos.
- **Equipo**: ver a la recepción con sus permisos; invitar a alguien nuevo (su invitación aparece en
  http://localhost:55424; también sirve `python scripts/local.py clave <email>`).
- **Turnos**: después de reservar por el bot (siguiente punto), acá aparece el turno. La Coloración queda
  **pendiente**: probá **Aprobar** y **Rechazar**.
- Entrá en otra ventana privada con `recepcion@turnos.test`: solo ve lo permitido.

### 3. Bot de Telegram (simulador)
Abrí http://localhost:8120. Arriba elegís el **negocio** y el **cliente ficticio**; abajo escribís o tocás botones. Probá:
1. Escribí `hola`. El bot pide el teléfono: tocá **Compartir mi contacto**, después escribí tu nombre.
2. `reservar` (o el botón "Reservar turno"): elegí servicio, profesional y horario, y confirmá. Con la **Coloración**
   el turno queda pendiente de aprobación; con Corte o Manicura se confirma solo.
3. `mis turnos`: lista tus próximos turnos. Mirá el turno en el dashboard (Turnos). Si lo aprobás ahí, el cliente recibe el
   aviso en el simulador.
4. `servicios` (servicios y precios), `datos del local` y `hablar con el negocio`.
5. `mis datos`: muestra qué datos guardó el sistema sobre vos.
6. `borrar mis datos`: pide confirmación y anonimiza tu perfil (y cancela tus turnos pendientes). Probá después escribir `hola`:
   ofrece reactivar o empezar de cero. En el dashboard, ese cliente ya no muestra tus datos.
7. **+ Cliente** crea otro cliente ficticio (otro "celular") y **+ Bot** registra el bot de otro negocio.

### 4. Procesos automáticos (jobs)
- **Vencimiento de turnos pendientes**: corre solo cada 2 minutos. Un turno pendiente que no se aprueba antes de su horario
  se cancela y se avisa al cliente. Para verlo sin esperar: reservá una Coloración por el simulador y corré
  `python scripts/local.py vencer` (adelanta el vencimiento); en menos de 2 minutos el simulador muestra "Tu solicitud de
  turno venció sin respuesta del negocio y fue cancelada" y el turno aparece cancelado en el dashboard.
- **Purga de negocios dados de baja**: en producción corre una vez por día. Acá se corre a pedido con
  `python scripts/local.py purga` (si no hay negocios dados de baja, termina sin hacer nada). Para probarla de verdad:
  en el panel de administración, **dar de baja** un negocio y volver a correrla.

## Apagar, ver el estado y empezar de cero

| Comando | Qué hace |
|---|---|
| `python scripts/local.py down` | Apaga todo. **Los datos se conservan**: el próximo `up` sigue donde quedó. |
| `python scripts/local.py status` | Muestra qué está corriendo. |
| `python scripts/local.py logs bot` | Últimas líneas del registro de un servicio (`bot`, `dashboard-backend`, `admin-backend`, `job-vencimiento`, `telegram-sim`…). |
| `python scripts/local.py reset` | Apaga, **borra todos los datos locales** (base, usuarios, conversaciones del simulador) y vuelve a levantar de cero con los datos de demo. |

Dejá `down` cuando termines: los contenedores consumen memoria.

## Usar un bot real de Telegram (opcional)

Con el simulador alcanza para probar. Si querés chatear desde tu celular con un bot de verdad, Telegram tiene que poder
llamar a tu computadora, y para eso hace falta un **túnel** (un servicio que publica `localhost` en internet).

1. En Telegram, hablá con **@BotFather** > `/newbot` y guardá el **token** (es un secreto: no lo pegues en chats ni
   archivos del repositorio).
2. Instalá un túnel, por ejemplo `cloudflared` (`cloudflared tunnel --url http://localhost:8110`), que te da una dirección
   `https://….trycloudflare.com`. Dejalo corriendo.
3. En `.env.local` dejá **vacía** la línea `TELEGRAM_API_BASE_URL=` (así el bot habla con el Telegram real, no con el
   simulador) y corré `python scripts/local.py up`. `up` respeta ese valor mientras no hagas `reset`.
4. En el panel de administración, ficha del negocio > **Telegram**: cargá el token real y un secret inventado (letras,
   números, `_` o `-`). Anotá la `webhook_key`.
5. Le avisás a Telegram dónde está tu bot (en una terminal, escribiendo el token y el secret cuando los pida, para que no
   queden en el historial):
   ```
   read -rs BOT_TOKEN; read -rs WEBHOOK_SECRET
   curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
     --data-urlencode "url=https://<tu-túnel>/api/bot/telegram/<webhook_key>" \
     --data-urlencode "secret_token=${WEBHOOK_SECRET}"
   ```
6. Escribile a tu bot desde el celular. Cuando termines, volvé a poner `TELEGRAM_API_BASE_URL=http://telegram-sim:8120` y
   corré `up` de nuevo (o `reset`).

## Si algo no anda

- **"Docker no está corriendo"**: abrí Docker Desktop y esperá a que diga "Engine running".
- **El `up` falla a la mitad**: volvé a correrlo (es seguro repetirlo). Si sigue, `python scripts/local.py logs` y mirá
  el último error; en último caso `reset`.
- **La pantalla no carga la primera vez**: los dos frontends instalan sus paquetes al arrancar; el `up` espera hasta 10
  minutos. Después carga al instante.
- **"No puedo entrar"**: revisá el email y la contraseña (son las de la tabla) y que el código de MFA sea el vigente.
- **El simulador dice "El bot respondió 401"**: el secret cargado en el simulador no es el de la integración del
  negocio; volvé a registrarlo con **+ Bot**.

## Cómo está armado (para quien quiera mirar)

- `scripts/local.py`: el comando (`up`, `down`, `reset`…). Levanta Supabase local con su CLI (configuración propia en
  `.local/`, generada a partir de `supabase/config.toml` con otros puertos), genera `.env.local` y orquesta
  `local/docker-compose.yml`.
- `local/docker-compose.yml`: servicios de la app. Python 3.12 y Node corren en contenedores con el código del repo montado;
  los servicios reciben solo las variables que necesitan.
- `scripts/local_demo.py`: crea la cuenta de Franco, corre `scripts/setup_proyecto.py` (roles, clave HMAC, primer
  administrador) y carga el negocio de demo **usando las mismas APIs que usan las pantallas**.
- `tools/telegram_sim/`: el simulador. Hace de Bot API (el bot lo usa por la variable `TELEGRAM_API_BASE_URL`, que en
  producción no existe y vale la dirección oficial) y manda los mensajes al webhook real del bot con el secret correcto.
- Las cookies de sesión usan `SameSite=Strict`; en `localhost` todo es el mismo sitio entre puertos, así que funciona igual
  que en producción. Solo en local se desactiva `Secure` (porque es `http`).
