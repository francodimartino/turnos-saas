# web-backend

FastAPI (Render). API pública de la web de reservas (ARCHITECTURE.md §2.12): sin cuenta ni sesión para leer. Es lo más
expuesto del sistema, así que entra a Postgres con su propio rol de mínimo privilegio, `web_reservas` (§3.2), y no
comparte pool ni credenciales con el dashboard, el bot ni el enviador.

Arranque: `uvicorn web_backend.main:crear_app_desde_entorno --factory --no-server-header` (variables en `config.py`; si
falta alguna o es insegura, el proceso no arranca).

## Piezas

- `config.py`: configuración por entorno, validada al arrancar. `WEB_DATABASE_URL` (rol `web_reservas`), `WEB_ORIGIN`
  (único origen permitido por CORS: `https` y sin comodines; `http` solo hacia una máquina local), `SENTRY_DSN`
  (proyecto `turnos-web-backend`) y, juntas o ninguna, `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` para firmar la URL
  del logo.
- `app.py`: fábrica de la app. Middlewares, de afuera hacia adentro: log de la petición (sin query ni cuerpo), headers de
  seguridad y captura de excepciones (`turnos_seguridad_http`), CORS de un solo origen (solo `GET`), límite de cuerpo
  (`HTTP_MAX_BODY_BYTES`, 16 KiB por defecto) y de URL (2048 bytes, `entrada.py`). `/docs`, `/redoc` y `/openapi.json`
  están apagados. `GET /health` da 200 solo si Postgres responde `select 1` (503 sin detalles, con timeout de 3 s).
- `rutas_negocio.py`: router de módulo, solo traduce HTTP. El dominio no conoce FastAPI: `negocio.py` (resolver el slug,
  armar la página, firmar el logo) y `huecos.py` (rango y cálculo con `turnos_dominio.calcular_huecos`); el SQL vive en
  `consultas.py`. `respuestas_negocio.py` son los modelos de salida.
- `dependencias.py`: pool, reloj y cliente de Storage desde `app.state`; la transacción de cada negocio es `tenant_tx`
  con el tenant que resolvió la base, nunca uno que venga del pedido.

## Endpoints

| Método y ruta | Resultado |
|---|---|
| `GET /negocios/{slug}` | `{nombre, slug, logo, color_principal, color_secundario, direccion, zona_horaria, servicios[{id, nombre, duracion_minutos, precio, profesionales}], profesionales[{id, nombre, servicios}]}`. `logo` es una URL firmada de una hora (o `null`). Solo salen las claves de `settings` de `negocio.CLAVES_PUBLICAS_DE_SETTINGS`. |
| `GET /negocios/{slug}/huecos?servicio_id=&profesional_id=&desde=&hasta=` | `{zona_horaria, huecos[{inicio, fin}]}`. Fechas `AAAA-MM-DD` del calendario del negocio, `hasta` inclusive; hasta 31 días por pedido y hasta 90 días desde hoy. La anticipación mínima es `booking_min_notice_minutes` del negocio. |
| `GET /health` | `{"estado": "ok"}` o 503 `{"estado": "no_disponible"}`. |

Un slug inexistente, de un negocio suspendido o dado de baja, reservado o con forma inválida da el mismo
`404 {"detail": {"codigo": "no_disponible"}}`: la base no los distingue y la respuesta tampoco. Un slug anterior (dentro de
sus 180 días) responde `301` con `Location` al pedido equivalente en el slug vigente y `slug_vigente` en el cuerpo.

## Errores

Todo error es `{"detail": {"codigo", "mensaje"}}`, sin repetir lo recibido.

| Estado | Código | Cuándo |
|---|---|---|
| 301 | `slug_redirigido` | Slug anterior vigente: `Location` al pedido equivalente en el slug vigente. |
| 404 | `no_disponible` | Slug que no resuelve (ver arriba). |
| 404 | `no_encontrado` | Ruta inexistente, o servicio o profesional que no se puede reservar en ese negocio. |
| 405 | `metodo_no_permitido` | Cualquier método que no sea `GET`. |
| 413 | `cuerpo_demasiado_grande` | Cuerpo por encima del límite. |
| 414 | `url_demasiado_larga` | Ruta más query por encima de 2048 bytes. |
| 422 | `parametros_invalidos` | Parámetro ausente, mal formado o demasiado largo; el cuerpo trae la lista de nombres de los parámetros con problema. |
| 422 | `fecha_invalida`, `rango_invalido`, `rango_demasiado_grande`, `fecha_fuera_de_rango` | Fechas del pedido de huecos. |
| 503 | `base_no_disponible`, `servicio_ocupado` | Base caída, rol sin permiso, pool agotado o consulta vencida (con `Retry-After`). |
| 500 | `error_interno` | Excepción no prevista: respuesta genérica; el detalle va solo a Sentry, sin datos personales. |

## Tests

- Unitarios (`tests/test_*_unitario.py`): pool y conexión falsos que devuelven lo mismo que la base real.
- Integración (`tests/test_integracion.py`, marca `integracion`): contra Postgres con el rol `web_reservas` real, con
  dos negocios para probar el aislamiento.
