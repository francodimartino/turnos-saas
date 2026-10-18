"""Recorrido end-to-end del piloto contra Postgres real (bot, dashboard, admin, jobs de vencimiento y purga).

Las tres apps FastAPI reales (`bot_app`, `dashboard_backend`, `admin_backend`) corren en proceso, cada una con el
pool de su rol real (`bot_service`, `dashboard_service`, `platform_admin` y `platform_admin_readonly`) y los jobs con
`scheduler_job` y `purge_job`. Solo lo que sale de la base está simulado: Telegram (transporte httpx), Google
Calendar, la Auth Admin API (invitar y eliminar cuentas) y el proxy de Supabase Auth. Los JWT se firman con un
secreto HS256 generado en cada corrida, y la verificación en vivo del estado del tenant y del staff es la real.

Hay dos negocios en paralelo (A y B) y en cada etapa se afirma que ninguno ve ni toca datos del otro: por la API,
por la RLS de cada rol y comprobando que los datos del otro siguen intactos.

Etapas:
  1. Franco (platform_admin) da de alta los tenants con su owner y carga la integración `telegram_bot`.
  2. El owner carga servicio, profesional (modo `approval`), reglas de disponibilidad y datos del local.
  3. Un cliente escribe al bot por el webhook real: comparte contacto, ve servicios y reserva (`pending`).
  4. El owner aprueba desde el dashboard: `confirmed` y aviso al cliente.
  5. Otro turno `pending` se deja vencer: el job de vencimiento lo cancela y avisa.
  6. "Borrar mis datos" desde el bot: anonimiza, cancela los `pending` futuros y respeta los `confirmed`.
  7. Franco da de baja el tenant, vence el período de gracia y el job de purga lo deja `purged` (slug liberado).

El test es un solo recorrido secuencial (cada etapa depende de lo que dejó la anterior); si una afirmación falla,
el mensaje dice en qué etapa. Teléfonos, nombres y correos son inventados.
"""

import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import httpx
import jwt
import pytest
from admin_backend.app import crear_app as crear_app_admin
from admin_backend.tests.soporte import SupabaseSimulado as SupabaseSimuladoAdmin
from admin_backend.tests.soporte import config_prueba as config_admin
from bot_app.tests import test_reserva_integracion as reserva
from bot_app.tests import test_webhook_integracion as webhook
from dashboard_backend.app import crear_app as crear_app_dashboard
from dashboard_backend.rutas_turnos import OpcionesAvisos
from dashboard_backend.tests.soporte import ORIGEN as ORIGEN_DASHBOARD
from dashboard_backend.tests.soporte import SupabaseSimulado as SupabaseSimuladoDashboard
from dashboard_backend.tests.soporte import config_prueba as config_dashboard
from jobs_purga import ejecutar as ejecutar_purga
from jobs_purga import exit_code as codigo_de_salida_purga
from jobs_vencimiento import ejecutar as ejecutar_vencimiento
from platform_auth import VerificadorJWT
from platform_calendar import AccionSync, ResultadoSync
from platform_tenancy import crear_pool, tenant_tx
from turnos_negocio import mensaje_vencimiento

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
AUD = "authenticated"
CABECERA_SECRET = webhook.CABECERA

# Fixtures reutilizadas de los tests de cada app (la clave HMAC de Vault que necesita el alta de clientes).
clave_hmac = reserva.clave_hmac

ROLES_POOL = (
    "bot_service",
    "dashboard_service",
    "platform_admin",
    "platform_admin_readonly",
    "scheduler_job",
    "purge_job",
)

# Tablas con `tenant_id` que se revisan por RLS (solo las que el rol puede leer).
TABLAS_CON_TENANT = (
    "end_customers",
    "bookings",
    "booking_packs",
    "notifications_log",
    "payments",
    "bot_sessions",
    "inbound_messages",
    "services",
    "professionals",
    "professional_services",
    "availability_rules",
    "tenant_integrations",
    "tenant_users",
    "audit_log",
)

TEL_C1, TEL_C2, TEL_B1 = "5491100000301", "5491100000302", "5491100000311"
NOMBRE_C1, NOMBRE_C2, NOMBRE_B1 = "Ana Pruebas", "Carla Ejemplo", "Beto Otro"
CORREO_PAGADOR_C1 = "ana.pruebas@ejemplo.invalid"
NOTA_C1, NOTA_C2 = "alergia al latex de Ana", "prefiere la silla del fondo (Carla)"
USUARIO_C1, USUARIO_C2, USUARIO_B1 = "7001", "7002", "7001"  # B1 usa el mismo id de Telegram que C1, en otro tenant


# --- piezas simuladas --------------------------------------------------------------------------------------------


class Telegram(webhook.Telegram):
    """Bot API simulada: registra cada llamada y entrega un cliente httpx que la atiende."""

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def a_chat(self, chat: str) -> list[dict]:
        return [
            ll for ll in self.llamadas if ll["cuerpo"].get("chat_id") == chat and ll["url"].endswith("/sendMessage")
        ]


class GoogleSimulado:
    """Google Calendar simulado: crea eventos con id propio y anota qué pidieron limpiar y con qué estado de la base."""

    def __init__(self, db) -> None:
        self.db = db
        self.sincronizados: list = []
        self.pares_limpiados: list = []
        self.telefono_al_limpiar: list = []

    async def sincronizar_booking(self, booking):
        self.sincronizados.append(booking)
        evento = booking.google_calendar_event_id
        if booking.status == "cancelled":
            return ResultadoSync(AccionSync.BORRADO if evento else AccionSync.IGNORADO, None)
        return ResultadoSync(
            AccionSync.ACTUALIZADO if evento else AccionSync.CREADO, evento or f"evt-{uuid.uuid4().hex}"
        )

    async def limpiar_eventos(self, pares):
        self.pares_limpiados += list(pares)
        self.telefono_al_limpiar.append(
            [f["phone"] for f in await self.db.fetch("select phone from public.end_customers where phone is not null")]
        )
        return []

    @asynccontextmanager
    async def fabrica_dashboard(self, _conn):
        yield self


class AuthAdminSimulada:
    """Auth Admin API simulada: invitar crea la cuenta en `auth.users` y eliminar la borra, como la real."""

    def __init__(self, db) -> None:
        self.db = db
        self.por_email: dict[str, uuid.UUID] = {}
        self.cuentas: list[uuid.UUID] = []
        self.eliminadas: list[uuid.UUID] = []

    async def invitar(self, email: str, nombre: str | None = None) -> uuid.UUID:
        cuenta = uuid.uuid4()
        await self.db.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values ($1, $2, $3::jsonb)",
            cuenta,
            email,
            json.dumps({"full_name": nombre} if nombre else {}),
        )
        self.por_email[email] = cuenta
        self.cuentas.append(cuenta)
        return cuenta

    async def eliminar_usuario(self, auth_user_id: uuid.UUID) -> bool:
        self.eliminadas.append(auth_user_id)
        borrada = await self.db.fetchval("delete from auth.users where id = $1 returning id", auth_user_id)
        return borrada is not None


class Reportes:
    """Destino de alertas de los jobs (Sentry en producción): guarda todo para afirmar que no hubo fallos propios."""

    def __init__(self) -> None:
        self.items: list = []

    def __call__(self, descripcion, **etiquetas):
        self.items.append((descripcion, etiquetas))

    def capturar_excepcion(self, exc, **contexto):
        self.items.append((repr(exc), contexto))

    def capturar_mensaje(self, mensaje, **contexto):
        self.items.append((mensaje, contexto))

    def menciona(self, *tenants) -> bool:
        return any(str(t) in str(item) for item in self.items for t in tenants)


async def _sin_espera(_segundos):
    return None


# --- el mundo de la prueba ---------------------------------------------------------------------------------------


@dataclass
class Negocio:
    """Un tenant del recorrido: lo que se pide en el alta y lo que se va descubriendo."""

    etiqueta: str
    nombre: str
    slug: str
    owner_email: str
    servicio_nombre: str
    profesional_nombre: str
    direccion: str
    bot_token: str = field(default_factory=lambda: f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(32)}")
    webhook_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    tenant_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    webhook_key: str | None = None
    servicio_id: uuid.UUID | None = None
    profesional_id: uuid.UUID | None = None
    regla_id: uuid.UUID | None = None
    integracion_google: uuid.UUID | None = None
    turnos: list[uuid.UUID] = field(default_factory=list)

    @property
    def ruta(self) -> str:  # lo que usa `Bot` de los tests del bot
        return f"/api/bot/telegram/{self.webhook_key}"


class Mundo:
    """Apps, pools y simuladores del recorrido (se arma en la fixture `mundo`)."""

    db: asyncpg.Connection
    pools: dict
    admin: httpx.AsyncClient
    dash: httpx.AsyncClient
    bot: reserva.Bot
    google_bot: GoogleSimulado
    google_dashboard: GoogleSimulado
    google_purga: GoogleSimulado
    tg_dashboard: Telegram
    tg_job: Telegram
    auth: AuthAdminSimulada
    franco: uuid.UUID
    secreto_jwt: str
    tenants: list
    cuentas: list

    def token(self, **claims) -> str:
        base = {"aud": AUD, "exp": int(time.time()) + 600, "aal": "aal2", "tenant_id": None, "is_owner": False}
        base.update({k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in claims.items()})
        return jwt.encode(base, self.secreto_jwt, algorithm="HS256")

    @property
    def cab_franco(self) -> dict[str, str]:
        t = self.token(sub=self.franco, is_platform_admin=True)
        return {"Authorization": f"Bearer {t}"}

    def cab_owner(self, n: Negocio) -> dict[str, str]:
        """Cookie de sesión del dashboard + `Origin`, como lo manda el navegador."""
        t = self.token(sub=n.owner_id, tenant_id=n.tenant_id, is_owner=True, is_platform_admin=False)
        return {"Cookie": f"dash_acceso={t}", "Origin": ORIGEN_DASHBOARD}


@pytest.fixture
async def mundo(database_url, roles_password, db_superuser, clave_hmac, monkeypatch):
    for variable in reserva.VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)  # ningún job puede llegar al Google real
    m = Mundo()
    m.db = db_superuser
    m.secreto_jwt = secrets.token_urlsafe(48)
    m.tenants, m.cuentas = [], []
    m.pools = {}
    clientes: list[httpx.AsyncClient] = []
    try:
        for rol in ROLES_POOL:
            dsn = webhook._dsn(database_url, rol, roles_password)
            m.pools[rol] = await crear_pool(dsn, min_size=1, max_size=4, statement_cache_size=0)

        m.franco = uuid.uuid4()
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", m.franco, "franco@ejemplo.invalid"
        )
        await db_superuser.execute("insert into public.platform_admins (auth_user_id) values ($1)", m.franco)
        m.cuentas.append(m.franco)

        m.auth = AuthAdminSimulada(db_superuser)
        m.google_bot, m.google_dashboard = GoogleSimulado(db_superuser), GoogleSimulado(db_superuser)
        m.google_purga = GoogleSimulado(db_superuser)
        m.tg_dashboard, m.tg_job = Telegram(), Telegram()

        verificador = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=m.secreto_jwt)
        app_admin = crear_app_admin(
            config_admin(),
            verificador=verificador,
            cliente_auth_admin=m.auth,
            pool=m.pools["platform_admin"],
            pool_readonly=m.pools["platform_admin_readonly"],
            cliente_auth=SupabaseSimuladoAdmin().cliente(),
            configurar_observabilidad=False,
        )
        http_dashboard = m.tg_dashboard.cliente()
        clientes.append(http_dashboard)
        app_dashboard = crear_app_dashboard(
            config_dashboard(),
            verificador=verificador,
            pool=m.pools["dashboard_service"],
            cliente_auth=SupabaseSimuladoDashboard().cliente(),
            fabrica_calendario=m.google_dashboard.fabrica_dashboard,
            opciones_avisos=OpcionesAvisos(cliente_http=http_dashboard, backoff=(0.0, 0.0, 0.0), esperar=_sin_espera),
            configurar_observabilidad=False,
        )
        m.admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_admin), base_url="http://admin.test")
        m.dash = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_dashboard), base_url="http://dash.test")
        clientes += [m.admin, m.dash]
        m.bot = reserva.Bot(m.pools["bot_service"], m.google_bot)
        clientes.append(m.bot.http)
        yield m
    finally:
        for c in clientes:
            await c.aclose()
        for pool in m.pools.values():
            await pool.close()
        await _limpiar(db_superuser, m.tenants, m.cuentas + m.auth.cuentas if hasattr(m, "auth") else m.cuentas)


async def _limpiar(db, tenants: list, cuentas: list) -> None:
    """Borra todo lo del recorrido sin disparar triggers (audit_log solo-agregar, último owner) ni FKs."""
    async with db.transaction():
        await db.execute("set local session_replication_role = replica")
        secretos = await db.fetch(
            "select secret_id from public.tenant_integrations "
            "where tenant_id = any($1::uuid[]) and secret_id is not null",
            tenants,
        )
        await db.execute("delete from vault.secrets where id = any($1::uuid[])", [s["secret_id"] for s in secretos])
        tablas = await db.fetch(
            "select c.table_name from information_schema.columns c "
            "join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name "
            "where c.table_schema = 'public' and c.column_name = 'tenant_id' and t.table_type = 'BASE TABLE'"
        )
        for tabla in tablas:
            await db.execute(f"delete from public.{tabla['table_name']} where tenant_id = any($1::uuid[])", tenants)
        await db.execute("delete from public.audit_log where actor_id = any($1::uuid[])", cuentas)
        await db.execute("delete from public.tenants where id = any($1::uuid[])", tenants)
        await db.execute("delete from public.platform_admins where auth_user_id = any($1::uuid[])", cuentas)
        await db.execute("delete from auth.users where id = any($1::uuid[])", cuentas)


# --- utilidades de lectura ---------------------------------------------------------------------------------------


def _ahora_iso(dias: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=dias)).isoformat()


async def _fila_cliente(db, tenant_id, usuario: str):
    return await db.fetchrow(
        "select * from public.end_customers where tenant_id = $1 and channel_user_id = $2", tenant_id, usuario
    )


async def _turnos_de(db, cliente_id) -> dict:
    filas = await db.fetch("select id, status from public.bookings where end_customer_id = $1", cliente_id)
    return {f["id"]: f["status"] for f in filas}


async def _jsonb(db, sql: str, *args) -> dict:
    valor = await db.fetchval(sql, *args)
    return json.loads(valor) if isinstance(valor, str) else valor


async def _pii_en_tablas(db, tenant_id, fragmentos: list[str]) -> list[str]:
    """Dónde aparece (como texto) alguno de los fragmentos de PII dentro de lo guardado del tenant."""
    hallazgos = []
    for tabla in ("end_customers", "notifications_log", "payments", "bot_sessions", "audit_log"):
        for fragmento in fragmentos:
            n = await db.fetchval(
                f"select count(*) from public.{tabla} t "
                "where t.tenant_id = $1 and position(lower($2) in lower(t::text)) > 0",
                tenant_id,
                fragmento,
            )
            if n:
                hallazgos.append(f"{tabla}: {fragmento!r} x{n}")
    return hallazgos


# --- aislamiento entre tenants -----------------------------------------------------------------------------------


async def verificar_aislamiento(
    m: Mundo, etapa: str, a: Negocio, b: Negocio, *, con_datos: bool, a_activo: bool = True
):
    """Ni la API ni la RLS de ningún rol dejan ver o tocar datos del otro tenant (se llama en cada etapa)."""
    # 1) RLS: con el contexto de un tenant, el otro no existe para dashboard_service ni para bot_service.
    for rol in ("dashboard_service", "bot_service"):
        for propio, ajeno in ((a, b), (b, a)):
            async with tenant_tx(m.pools[rol], propio.tenant_id) as conn:
                for tabla in TABLAS_CON_TENANT:
                    if not await m.db.fetchval("select has_table_privilege($1, $2, 'select')", rol, f"public.{tabla}"):
                        continue
                    visibles = await conn.fetchval(
                        f"select count(*) from public.{tabla} where tenant_id = $1", ajeno.tenant_id
                    )
                    assert visibles == 0, (
                        f"[{etapa}] {rol} con el contexto de {propio.etiqueta} ve {tabla} de {ajeno.etiqueta}"
                    )
                if await m.db.fetchval("select has_table_privilege($1, 'public.tenants', 'select')", rol):
                    fila = await conn.fetchval("select count(*) from public.tenants where id = $1", ajeno.tenant_id)
                    assert fila == 0, (
                        f"[{etapa}] {rol} con el contexto de {propio.etiqueta} ve el tenant {ajeno.etiqueta}"
                    )
    if con_datos:  # la comprobación anterior no vale si el otro tenant no tuviera filas
        for n in (a, b):
            assert await m.db.fetchval("select count(*) from public.end_customers where tenant_id = $1", n.tenant_id)

    # 2) API del dashboard: cada owner ve solo lo suyo y lo ajeno es un 404 que no cambia nada.
    for propio, ajeno in ((a, b), (b, a)):
        if propio is a and not a_activo:
            continue
        h = m.cab_owner(propio)
        listados = {
            "/servicios": propio.servicio_id,
            "/profesionales": propio.profesional_id,
            "/reglas-disponibilidad": propio.regla_id,
        }
        for ruta, esperado in listados.items():
            r = await m.dash.get(ruta, headers=h)
            assert r.status_code == 200, f"[{etapa}] GET {ruta}: {r.text}"
            assert {x["id"] for x in r.json()} == {str(esperado)}, f"[{etapa}] {propio.etiqueta} lista {ruta} ajenos"
        for ruta in (f"/servicios/{ajeno.servicio_id}", f"/profesionales/{ajeno.profesional_id}"):
            r = await m.dash.get(ruta, headers=h)
            assert r.status_code == 404, f"[{etapa}] {propio.etiqueta} lee {ruta} de {ajeno.etiqueta}: {r.text}"
        r = await m.dash.patch(f"/servicios/{ajeno.servicio_id}", json={"name": "tomado"}, headers=h)
        assert r.status_code == 404, f"[{etapa}] {propio.etiqueta} edita un servicio de {ajeno.etiqueta}: {r.text}"
        nombre = await m.db.fetchval("select name from public.services where id = $1", ajeno.servicio_id)
        assert nombre == ajeno.servicio_nombre, f"[{etapa}] se cambió el servicio de {ajeno.etiqueta}"
        r = await m.dash.get("/clientes", headers=h)
        assert r.status_code == 200, r.text
        nombres_propios = {NOMBRE_C1, NOMBRE_C2} if propio is a else {NOMBRE_B1}
        nombres_ajenos = {NOMBRE_B1} if propio is a else {NOMBRE_C1, NOMBRE_C2}
        vistos = {c["name"] for c in r.json()["clientes"] if c["name"]}
        assert vistos <= nombres_propios and not vistos & nombres_ajenos, (
            f"[{etapa}] clientes ajenos en {propio.etiqueta}"
        )
        r = await m.dash.get("/turnos", params={"desde": _ahora_iso(-1), "hasta": _ahora_iso(30)}, headers=h)
        assert r.status_code == 200, r.text
        servicios_vistos = {t["service_id"] for t in r.json()["turnos"]}
        assert servicios_vistos <= {str(propio.servicio_id)}, f"[{etapa}] turnos ajenos en {propio.etiqueta}"
        for turno in ajeno.turnos:
            for accion in ("aprobar", "cancelar"):
                r = await m.dash.post(f"/turnos/{turno}/{accion}", headers=h)
                assert r.status_code == 404, (
                    f"[{etapa}] {propio.etiqueta} hace {accion} sobre un turno de {ajeno.etiqueta}"
                )


# --- etapa 1: alta de tenants e integración ----------------------------------------------------------------------


async def etapa_1_alta_y_telegram(m: Mundo, n: Negocio) -> None:
    r = await m.admin.post(
        "/tenants",
        json={"name": n.nombre, "slug": n.slug, "owner_email": n.owner_email, "owner_nombre": "Dueña de Prueba"},
        headers=m.cab_franco,
    )
    assert r.status_code == 201, f"[1] alta de {n.etiqueta}: {r.text}"
    assert r.json()["estado_invitacion"] == "ok" and n.owner_email not in r.text
    n.tenant_id = uuid.UUID(r.json()["tenant_id"])
    m.tenants.append(n.tenant_id)
    n.owner_id = m.auth.por_email[n.owner_email]  # la invitación (simulada) creó la cuenta del owner
    owner = await m.db.fetchrow(
        "select auth_user_id, is_owner, is_active from public.tenant_users where tenant_id = $1", n.tenant_id
    )
    assert (owner["auth_user_id"], owner["is_owner"], owner["is_active"]) == (n.owner_id, True, True)
    ficha = await m.admin.get(f"/tenants/{n.tenant_id}", headers=m.cab_franco)
    assert ficha.status_code == 200, ficha.text
    assert (ficha.json()["owner_nombre"], ficha.json()["owner_email"]) == ("Dueña de Prueba", n.owner_email)

    secreto = json.dumps({"bot_token": n.bot_token, "webhook_secret": n.webhook_secret})
    r = await m.admin.put(
        f"/tenants/{n.tenant_id}/integraciones/telegram_bot",
        json={"config": {}, "secret": secreto},
        headers=m.cab_franco,
    )
    assert r.status_code == 200, f"[1] integración de {n.etiqueta}: {r.text}"
    assert n.bot_token not in r.text and n.webhook_secret not in r.text  # la credencial no vuelve
    n.webhook_key = r.json()["webhook_key"]
    guardado = await m.db.fetchrow(
        "select config::text as config, secret_id from public.tenant_integrations "
        "where tenant_id = $1 and integration_type = 'telegram_bot'",
        n.tenant_id,
    )
    assert guardado["secret_id"] is not None and n.bot_token not in guardado["config"]


# --- etapa 2: catálogo, disponibilidad y datos del local ---------------------------------------------------------


async def etapa_2_catalogo(m: Mundo, n: Negocio) -> None:
    h = m.cab_owner(n)
    r = await m.dash.post(
        "/servicios", json={"name": n.servicio_nombre, "duration_minutes": 30, "price": 5000}, headers=h
    )
    assert r.status_code == 201, f"[2] servicio de {n.etiqueta}: {r.text}"
    n.servicio_id = uuid.UUID(r.json()["id"])
    r = await m.dash.post("/profesionales", json={"name": n.profesional_nombre}, headers=h)
    assert r.status_code == 201, f"[2] profesional de {n.etiqueta}: {r.text}"
    n.profesional_id = uuid.UUID(r.json()["id"])

    ruta = f"/profesionales/{n.profesional_id}/servicios/{n.servicio_id}"
    r = await m.dash.put(ruta, json={"confirmation_mode": "deposit"}, headers=h)
    assert r.status_code == 422, f"[2] el modo deposit no existe en el piloto: {r.text}"
    r = await m.dash.put(ruta, json={"confirmation_mode": "approval"}, headers=h)
    assert r.status_code == 200 and r.json()["confirmation_mode"] == "approval", f"[2] {r.text}"

    _, dia_semana = reserva._dia_de_atencion()
    r = await m.dash.post(
        "/reglas-disponibilidad",
        json={
            "professional_id": str(n.profesional_id),
            "day_of_week": dia_semana,
            "start_time": "10:00",
            "end_time": "12:00",
        },
        headers=h,
    )
    assert r.status_code == 201, f"[2] regla de {n.etiqueta}: {r.text}"
    n.regla_id = uuid.UUID(r.json()["id"])

    r = await m.dash.patch(
        "/local/settings",
        json={
            "settings": {
                "local_direccion": n.direccion,
                "local_horarios": ["Lunes a viernes de 10 a 12"],
                "local_contacto": f"Llamar al local de {n.nombre}",
            }
        },
        headers=h,
    )
    assert r.status_code == 200, f"[2] settings de {n.etiqueta}: {r.text}"
    assert r.json()["settings"]["local_direccion"] == n.direccion


# --- etapa 3: reserva por el bot ---------------------------------------------------------------------------------


async def _reservar_con_aprobacion(m: Mundo, n: Negocio, usuario: str) -> str:
    """Reserva el primer horario libre del servicio en modo `approval`. Devuelve el botón de horario elegido."""
    llamadas = await m.bot.texto(n, usuario, "reservar")
    assert f"rsv:s:{n.servicio_id}" in reserva.botones(llamadas), "el bot no ofrece el servicio"
    await m.bot.boton(n, usuario, f"rsv:s:{n.servicio_id}")
    llamadas = await m.bot.boton(n, usuario, f"rsv:p:{n.profesional_id}")
    horarios = [b for b in reserva.botones(llamadas) if b.startswith("rsv:h:")]
    assert horarios, "el bot no ofrece horarios"
    await m.bot.boton(n, usuario, horarios[0])
    llamadas = await m.bot.boton(n, usuario, "rsv:ok")
    assert "pendiente de aprobación" in reserva.textos(llamadas)
    return horarios[0]


async def etapa_3_reserva_por_bot(m: Mundo, a: Negocio, b: Negocio) -> uuid.UUID:
    # El webhook rechaza lo que no trae el secret de ESE tenant (y no deja nada escrito).
    marcas = await m.db.fetchval("select count(*) from public.inbound_messages where tenant_id = $1", a.tenant_id)
    cuerpo = {"update_id": 1, "message": {"message_id": 1, "from": {"id": 1}, "chat": {"id": 1}, "text": "hola"}}
    for secreto in (None, "no-es-el-secret", b.webhook_secret):
        cab = {} if secreto is None else {CABECERA_SECRET: secreto}
        r = await m.bot.http.post(a.ruta, json=cuerpo, headers=cab)
        assert r.status_code == 401, f"[3] el webhook de A aceptó un secret ajeno: {r.status_code}"
    r = await m.bot.http.post(f"/api/bot/telegram/{uuid.uuid4()}", json=cuerpo, headers={CABECERA_SECRET: "x"})
    assert r.status_code == 200  # clave desconocida: se descarta sin que Telegram reintente
    assert (
        await m.db.fetchval("select count(*) from public.inbound_messages where tenant_id = $1", a.tenant_id) == marcas
    )

    # C1 se da de alta paso a paso y ve el catálogo de SU negocio.
    llamadas = await m.bot.texto(a, USUARIO_C1, "hola")
    assert "teléfono" in reserva.textos(llamadas)
    teclado = llamadas[0]["cuerpo"]["reply_markup"]["keyboard"]
    assert teclado[0][0]["request_contact"] is True
    assert "cómo te llamás" in reserva.textos(await m.bot.contacto(a, USUARIO_C1, TEL_C1)).lower()
    assert "registrado" in reserva.textos(await m.bot.texto(a, USUARIO_C1, NOMBRE_C1))
    cliente_a = await _fila_cliente(m.db, a.tenant_id, USUARIO_C1)
    assert (cliente_a["phone"], cliente_a["name"]) == (f"+{TEL_C1}", NOMBRE_C1)

    servicios = reserva.textos(await m.bot.texto(a, USUARIO_C1, "servicios"))
    assert a.servicio_nombre in servicios and b.servicio_nombre not in servicios
    local = reserva.textos(await m.bot.texto(a, USUARIO_C1, "datos del local"))
    assert a.direccion in local and b.direccion not in local

    # El mismo usuario de Telegram, en el otro tenant, es otra persona con su propio perfil.
    await m.bot.alta(b, USUARIO_B1, TEL_B1, NOMBRE_B1)
    cliente_b = await _fila_cliente(m.db, b.tenant_id, USUARIO_B1)
    assert cliente_b["id"] != cliente_a["id"] and cliente_b["phone"] == f"+{TEL_B1}"
    servicios_b = reserva.textos(await m.bot.texto(b, USUARIO_B1, "servicios"))
    assert b.servicio_nombre in servicios_b and a.servicio_nombre not in servicios_b

    # Reservas en modo approval: nacen `pending` con vencimiento.
    await _reservar_con_aprobacion(m, a, USUARIO_C1)
    await _reservar_con_aprobacion(m, b, USUARIO_B1)
    (turno_a,) = [t for t, s in (await _turnos_de(m.db, cliente_a["id"])).items() if s == "pending"]
    (turno_b,) = [t for t, s in (await _turnos_de(m.db, cliente_b["id"])).items() if s == "pending"]
    a.turnos.append(turno_a)
    b.turnos.append(turno_b)
    fila = await m.db.fetchrow("select tenant_id, pending_expires_at from public.bookings where id = $1", turno_a)
    assert fila["tenant_id"] == a.tenant_id and fila["pending_expires_at"] is not None
    # el cliente recibió la confirmación de la solicitud, registrada en notifications_log
    registrada = await m.db.fetchval(
        "select count(*) from public.notifications_log where end_customer_id = $1 and template = 'reserva_pendiente'",
        cliente_a["id"],
    )
    assert registrada == 1
    return cliente_a["id"]


# --- etapa 4: aprobación desde el dashboard ----------------------------------------------------------------------


async def etapa_4_aprobacion(m: Mundo, a: Negocio, b: Negocio, c1: uuid.UUID) -> None:
    turno = a.turnos[0]
    # El owner de B no puede ni ver ni aprobar el turno de A (404), y el turno de A sigue pendiente.
    r = await m.dash.post(f"/turnos/{turno}/aprobar", headers=m.cab_owner(b))
    assert r.status_code == 404, f"[4] B aprobó un turno de A: {r.text}"
    assert (await _turnos_de(m.db, c1))[turno] == "pending"

    h = m.cab_owner(a)
    r = await m.dash.get(
        "/turnos", params={"desde": _ahora_iso(-1), "hasta": _ahora_iso(30), "estado": "pending"}, headers=h
    )
    assert r.status_code == 200, r.text
    (listado,) = r.json()["turnos"]
    assert listado["id"] == str(turno) and listado["customer_name"] == NOMBRE_C1

    r = await m.dash.post(f"/turnos/{turno}/aprobar", headers=h)
    assert r.status_code == 200, f"[4] aprobación: {r.text}"
    cuerpo = r.json()
    assert cuerpo["status"] == "confirmed" and cuerpo["aviso"] == "programado", cuerpo
    assert cuerpo["calendario"]["estado"] != "error", cuerpo
    fila = await m.db.fetchrow(
        "select status, pending_expires_at, google_calendar_event_id from public.bookings where id = $1", turno
    )
    assert fila["status"] == "confirmed" and fila["pending_expires_at"] is None
    assert fila["google_calendar_event_id"] and len(m.google_dashboard.sincronizados) == 1  # Google (simulado)

    # El cliente recibió el aviso por el bot de SU negocio; nadie más recibió nada.
    avisos = m.tg_dashboard.a_chat(USUARIO_C1)
    assert len(avisos) == 1 and f"/bot{a.bot_token}/" in avisos[0]["url"], "[4] el aviso no salió por el bot de A"
    assert "confirmado" in avisos[0]["cuerpo"]["text"] and a.servicio_nombre in avisos[0]["cuerpo"]["text"]
    assert all(b.bot_token not in ll["url"] for ll in m.tg_dashboard.llamadas), "[4] se usó el bot de B"
    log = await m.db.fetchrow(
        "select status, last_error from public.notifications_log "
        "where end_customer_id = $1 and template = 'turno_confirmado'",
        c1,
    )
    assert (log["status"], log["last_error"]) == ("sent", None)
    # el turno de B no se tocó
    assert await m.db.fetchval("select status from public.bookings where id = $1", b.turnos[0]) == "pending"
    # y el cliente ya lo ve confirmado en el bot
    mis_turnos = reserva.textos(await m.bot.texto(a, USUARIO_C1, "mis turnos"))
    assert a.servicio_nombre in mis_turnos and "pendiente de confirmación" not in mis_turnos


# --- etapa 5: vencimiento de un pending --------------------------------------------------------------------------


async def etapa_5_vencimiento(m: Mundo, a: Negocio, b: Negocio) -> uuid.UUID:
    await m.bot.alta(a, USUARIO_C2, TEL_C2, NOMBRE_C2)
    await _reservar_con_aprobacion(m, a, USUARIO_C2)
    c2 = (await _fila_cliente(m.db, a.tenant_id, USUARIO_C2))["id"]
    (turno,) = await _turnos_de(m.db, c2)
    a.turnos.append(turno)
    antes_b = await m.db.fetchval("select pending_expires_at from public.bookings where id = $1", b.turnos[0])
    inicio_turno = await m.db.fetchval("select lower(time_range) from public.bookings where id = $1", turno)

    # Se fuerza el vencimiento de la solicitud (en producción pasa solo al llegar `pending_expires_at`).
    await m.db.execute(
        "update public.bookings set pending_expires_at = now() - interval '1 minute' where id = $1", turno
    )
    reportes = Reportes()
    resultado = await ejecutar_vencimiento(
        m.pools["scheduler_job"],
        cliente_http=m.tg_job.cliente(),
        backoff=(0.0, 0.0, 0.0),
        esperar=_sin_espera,
        reportar=reportes,
    )
    assert resultado.vencidos >= 1 and resultado.avisados >= 1
    assert not reportes.menciona(a.tenant_id, b.tenant_id), f"[5] el job reportó fallos propios: {reportes.items}"

    fila = await m.db.fetchrow("select status, pending_expires_at from public.bookings where id = $1", turno)
    assert (fila["status"], fila["pending_expires_at"]) == ("cancelled", None)
    # el cliente vencido recibió el aviso por el bot de A, con el texto sin datos personales
    avisos = m.tg_job.a_chat(USUARIO_C2)
    assert len(avisos) == 1 and f"/bot{a.bot_token}/" in avisos[0]["url"]
    assert avisos[0]["cuerpo"]["text"] == mensaje_vencimiento(inicio_turno, a.servicio_nombre)
    assert all(b.bot_token not in ll["url"] for ll in m.tg_job.llamadas), "[5] el job avisó por el bot de B"
    log = await m.db.fetchrow(
        "select status from public.notifications_log where end_customer_id = $1 and template = 'turno_vencido'", c2
    )
    assert log["status"] == "sent"
    # el turno confirmado de C1 y el pending (no vencido) de B siguen como estaban
    assert (await m.db.fetchval("select status from public.bookings where id = $1", a.turnos[0])) == "confirmed"
    assert (await m.db.fetchval("select status from public.bookings where id = $1", b.turnos[0])) == "pending"
    assert await m.db.fetchval("select pending_expires_at from public.bookings where id = $1", b.turnos[0]) == antes_b
    # un turno vencido no se puede aprobar después desde el dashboard
    r = await m.dash.post(f"/turnos/{turno}/aprobar", headers=m.cab_owner(a))
    assert r.status_code == 409, f"[5] se aprobó un turno vencido: {r.text}"
    return c2


# --- etapa 6: "borrar mis datos" ----------------------------------------------------------------------------------


async def _plantar_pii(m: Mundo, n: Negocio, cliente: uuid.UUID, turno: uuid.UUID, telefono: str, nota: str) -> None:
    """Copias de PII fuera de `end_customers` que la anonimización tiene que limpiar (§2.10, §3.5)."""
    await m.db.execute(
        "update public.end_customers set extra_data = $2::jsonb where id = $1", cliente, json.dumps({"nota": nota})
    )
    await m.db.execute(
        "insert into public.notifications_log (tenant_id, end_customer_id, channel, template, status, retry_count, "
        "last_error) values ($1, $2, 'telegram', 'recordatorio', 'failed', 3, $3)",
        n.tenant_id,
        cliente,
        f"chat not found para +{telefono}",
    )
    payload = {
        "id": 123,
        "status": "approved",
        "payer": {"email": CORREO_PAGADOR_C1, "first_name": "Ana", "phone": telefono},
        "additional_info": {"payer": {"first_name": "Ana", "phone": {"number": telefono}}, "items": ["sena"]},
        "card": {"cardholder": {"name": "ANA PRUEBAS"}, "last_four_digits": "4242"},
    }
    await m.db.execute(
        "insert into public.payments (tenant_id, payable_type, payable_id, provider_payment_id, amount, status, "
        "webhook_payload) values ($1, 'booking_deposit', $2, $3, 500, 'approved', $4::jsonb)",
        n.tenant_id,
        turno,
        f"mp-{uuid.uuid4().hex}",
        json.dumps(payload),
    )


async def _pagos_sin_pagador(m: Mundo, tenant_id) -> bool:
    """True si ningún pago del tenant conserva datos del pagador en `webhook_payload`."""
    for fila in await m.db.fetch("select webhook_payload from public.payments where tenant_id = $1", tenant_id):
        payload = (
            json.loads(fila["webhook_payload"]) if isinstance(fila["webhook_payload"], str) else fila["webhook_payload"]
        )
        if (
            "payer" in payload
            or "payer" in payload.get("additional_info", {})
            or "cardholder" in payload.get("card", {})
        ):
            return False
    return True


async def etapa_6_borrar_mis_datos(m: Mundo, a: Negocio, b: Negocio, c1: uuid.UUID, c2: uuid.UUID) -> None:
    # Google (simulado): el evento del turno confirmado de C1 es de un profesional con Calendar conectado.
    a.integracion_google = await m.db.fetchval(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2) returning id",
        a.tenant_id,
        a.profesional_id,
    )
    evento_c1 = await m.db.fetchval("select google_calendar_event_id from public.bookings where id = $1", a.turnos[0])
    b1 = (await _fila_cliente(m.db, b.tenant_id, USUARIO_B1))["id"]

    # C1 tiene, además del confirmado, un pending futuro. PII fuera de end_customers para C1, C2 y B1.
    await _reservar_con_aprobacion(m, a, USUARIO_C1)
    pendiente_c1 = next(t for t, s in (await _turnos_de(m.db, c1)).items() if s == "pending")
    a.turnos.append(pendiente_c1)
    await _plantar_pii(m, a, c1, a.turnos[0], TEL_C1, NOTA_C1)
    await _plantar_pii(m, a, c2, a.turnos[1], TEL_C2, NOTA_C2)
    await _plantar_pii(m, b, b1, b.turnos[0], TEL_B1, "nota de Beto")
    antes_b = await m.db.fetchrow("select * from public.end_customers where id = $1", b1)

    llamadas = await m.bot.texto(a, USUARIO_C1, "borrar mis datos")
    assert len(llamadas[0]["cuerpo"]["reply_markup"]["inline_keyboard"]) == 2  # pide confirmación
    assert (await _fila_cliente(m.db, a.tenant_id, USUARIO_C1))["phone"] == f"+{TEL_C1}"  # todavía no borró nada
    assert "borré tus datos" in reserva.textos(await m.bot.boton(a, USUARIO_C1, "idn:borrar_si"))

    # anonimizado: sin datos personales, con hash para el recontacto y marcado como borrado
    fila = await m.db.fetchrow("select * from public.end_customers where id = $1", c1)
    assert (fila["phone"], fila["name"], fila["channel_user_id"]) == (None, None, None)
    assert fila["extra_data"] in ("{}", {}) and fila["deleted_at"] is not None and fila["channel_user_hash"]
    # el pending futuro se canceló y el confirmado quedó intacto
    assert await _turnos_de(m.db, c1) == {a.turnos[0]: "confirmed", pendiente_c1: "cancelled"}
    # copias de PII limpias en notificaciones y pagos, sin sesión del bot
    assert (
        await m.db.fetchval(
            "select count(*) from public.notifications_log where end_customer_id = $1 and last_error is not null", c1
        )
        == 0
    )
    pago_c1 = await _jsonb(m.db, "select webhook_payload from public.payments where payable_id = $1", a.turnos[0])
    assert pago_c1["id"] == 123 and "payer" not in pago_c1 and "cardholder" not in pago_c1["card"]
    assert "payer" not in pago_c1["additional_info"] and pago_c1["additional_info"]["items"] == ["sena"]
    assert not await m.db.fetchval(
        "select count(*) from public.bot_sessions where tenant_id = $1 and channel_user_id = $2",
        a.tenant_id,
        USUARIO_C1,
    )
    # Google (simulado): se pidió limpiar el evento, con el borrado ya confirmado en la base
    assert m.google_bot.pares_limpiados == [(a.integracion_google, evento_c1)]
    assert f"+{TEL_C1}" not in m.google_bot.telefono_al_limpiar[0]
    assert not await m.db.fetchval(
        "select count(*) from public.audit_log where tenant_id = $1 and action = 'calendar_scrub_failed'", a.tenant_id
    )
    # el dashboard lo muestra sin cliente identificable
    r = await m.dash.get("/turnos", params={"desde": _ahora_iso(-1), "hasta": _ahora_iso(30)}, headers=m.cab_owner(a))
    propios = [t for t in r.json()["turnos"] if t["end_customer_id"] == str(c1)]
    assert len(propios) == 2 and all(t["sin_cliente_identificable"] and t["customer_name"] is None for t in propios)
    # lo de los demás no se tocó: C2 y todo el tenant B
    assert (await m.db.fetchrow("select phone, name from public.end_customers where id = $1", c2))["name"] == NOMBRE_C2
    assert dict(await m.db.fetchrow("select * from public.end_customers where id = $1", b1)) == dict(antes_b)
    assert (
        await m.db.fetchval(
            "select count(*) from public.notifications_log where end_customer_id = $1 and last_error is not null", b1
        )
        == 1
    )
    assert not await _pagos_sin_pagador(m, b.tenant_id)
    assert await m.db.fetchval("select status from public.bookings where id = $1", b.turnos[0]) == "pending"

    # Si vuelve a escribir, empieza de cero (se le ofrece reactivar su perfil anterior).
    assert "idn:reactivar" in reserva.botones(await m.bot.texto(a, USUARIO_C1, "hola"))
    # C2 queda con una conversación a medias (una sesión del bot con su identificador) para la purga
    await m.bot.texto(a, USUARIO_C2, "reservar")
    assert await m.db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", a.tenant_id) >= 2
    # y B1 también deja una conversación a medias, que la purga de A no puede tocar
    await m.bot.texto(b, USUARIO_B1, "reservar")
    assert await m.db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", b.tenant_id) == 1


# --- etapa 7: baja, período de gracia y purga --------------------------------------------------------------------


def _fabrica_purga(m: Mundo):
    return lambda _conn: m.google_purga


async def etapa_7_baja_y_purga(m: Mundo, a: Negocio, b: Negocio, c1: uuid.UUID, c2: uuid.UUID) -> None:
    b1 = (await _fila_cliente(m.db, b.tenant_id, USUARIO_B1))["id"]
    sesiones_b = await m.db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", b.tenant_id)
    secreto_b = await m.db.fetchval(
        "select secret_id from public.tenant_integrations where tenant_id = $1 and secret_id is not null", b.tenant_id
    )
    assert sesiones_b == 1

    r = await m.admin.post(f"/tenants/{a.tenant_id}/dar-de-baja", headers=m.cab_franco)
    assert r.status_code == 204, f"[7] baja: {r.text}"
    estado = await m.db.fetchrow("select status, deactivated_at from public.tenants where id = $1", a.tenant_id)
    assert estado["status"] == "inactive" and estado["deactivated_at"] is not None
    # con el negocio dado de baja: el bot descarta el webhook (200 sin procesar) y el dashboard lo rechaza
    marcas = await m.db.fetchval("select count(*) from public.inbound_messages where tenant_id = $1", a.tenant_id)
    antes = len(m.bot.telegram.llamadas)
    assert await m.bot.texto(a, USUARIO_C2, "hola") == [] and len(m.bot.telegram.llamadas) == antes
    assert (
        await m.db.fetchval("select count(*) from public.inbound_messages where tenant_id = $1", a.tenant_id) == marcas
    )
    r = await m.dash.get("/servicios", headers=m.cab_owner(a))
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "tenant_no_activo"
    # B sigue operando
    assert (await m.dash.get("/servicios", headers=m.cab_owner(b))).status_code == 200
    await verificar_aislamiento(m, "7 (tras la baja)", a, b, con_datos=True, a_activo=False)

    # Antes de que venza el período de gracia la purga no lo toca.
    auth = m.auth
    reportes = Reportes()
    resultado = await ejecutar_purga(m.pools["purge_job"], auth, _fabrica_purga(m), reportes)
    assert all(t.tenant_id != a.tenant_id for t in resultado.tenants), (
        "[7] purgó un tenant dentro del período de gracia"
    )
    assert await m.db.fetchval("select status from public.tenants where id = $1", a.tenant_id) == "inactive"

    # Control del detector: antes de la purga todavía hay PII de A (si no la hallara, la prueba posterior no valdría).
    antes_de_purgar = [TEL_C2, NOMBRE_C2, NOTA_C2]
    assert len(await _pii_en_tablas(m.db, a.tenant_id, antes_de_purgar)) >= 3

    # Se fuerza el vencimiento del período de gracia y corre el job de purga.
    await m.db.execute(
        "update public.tenants set deactivated_at = now() - interval '400 days' where id = $1", a.tenant_id
    )
    reportes = Reportes()
    resultado = await ejecutar_purga(m.pools["purge_job"], auth, _fabrica_purga(m), reportes)
    assert not reportes.menciona(a.tenant_id, b.tenant_id), f"[7] la purga reportó fallos: {reportes.items}"
    assert codigo_de_salida_purga(resultado) == 0
    assert [t.estado for t in resultado.tenants if t.tenant_id == a.tenant_id] == ["purged"]

    fila = await m.db.fetchrow("select status, slug from public.tenants where id = $1", a.tenant_id)
    assert fila["status"] == "purged" and fila["slug"] != a.slug and "-purged-" in fila["slug"]
    # solo se eliminó la cuenta del owner de A
    assert a.owner_id in auth.eliminadas and b.owner_id not in auth.eliminadas
    assert not await m.db.fetchval("select count(*) from auth.users where id = $1", a.owner_id)
    assert await m.db.fetchval("select count(*) from auth.users where id = $1", b.owner_id) == 1
    # Google (simulado): el paso 0 limpió el evento del turno confirmado
    assert any(par[0] == a.integracion_google for par in m.google_purga.pares_limpiados)
    # secrets de A borrados; el de B sigue
    assert (
        await m.db.fetchval(
            "select count(*) from public.tenant_integrations where tenant_id = $1 and secret_id is not null",
            a.tenant_id,
        )
        == 0
    )
    assert await m.db.fetchval("select count(*) from vault.secrets where id = $1", secreto_b) == 1

    # No queda PII de A en end_customers, notifications_log.last_error, payments.webhook_payload ni bot_sessions.
    clientes = await m.db.fetch(
        "select phone, name, extra_data, channel_user_id, deleted_at from public.end_customers where tenant_id = $1",
        a.tenant_id,
    )
    assert len(clientes) == 2
    for c in clientes:
        assert (c["phone"], c["name"], c["channel_user_id"]) == (None, None, None) and c["deleted_at"] is not None
        assert c["extra_data"] in ("{}", {})
    assert not await m.db.fetchval(
        "select count(*) from public.notifications_log where tenant_id = $1 and last_error is not null", a.tenant_id
    )
    assert await _pagos_sin_pagador(m, a.tenant_id)
    assert not await m.db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", a.tenant_id)
    fragmentos = [
        TEL_C1,
        TEL_C2,
        NOMBRE_C1,
        NOMBRE_C2,
        "ANA PRUEBAS",
        CORREO_PAGADOR_C1,
        NOTA_C1,
        NOTA_C2,
        a.owner_email,
    ]
    assert await _pii_en_tablas(m.db, a.tenant_id, fragmentos) == []
    # los turnos confirmados siguen, ya sin cliente identificable
    assert await _turnos_de(m.db, c1) == {a.turnos[0]: "confirmed", a.turnos[2]: "cancelled"}

    # El slug quedó libre: otro negocio puede darse de alta con el original.
    nuevo_email = f"nuevo-{uuid.uuid4().hex[:8]}@ejemplo.invalid"
    r = await m.admin.post(
        "/tenants",
        json={"name": "Negocio nuevo", "slug": a.slug, "owner_email": nuevo_email, "owner_nombre": "Otra Dueña"},
        headers=m.cab_franco,
    )
    assert r.status_code == 201, f"[7] el slug no quedó libre: {r.text}"
    m.tenants.append(uuid.UUID(r.json()["tenant_id"]))

    # El rastro de Franco: la baja y la purga quedaron en el audit_log de A.
    r = await m.admin.get(f"/tenants/{a.tenant_id}/audit-log", params={"limit": 500}, headers=m.cab_franco)
    assert r.status_code == 200, r.text
    acciones = {e["action"] for e in r.json()}
    assert {"tenant_create", "tenant_deactivate", "tenant_purge_step", "anonymize_end_customer"} <= acciones

    # Nada cambió en B: sus datos, su bot y su dashboard siguen igual.
    assert await m.db.fetchval("select status from public.tenants where id = $1", b.tenant_id) == "active"
    fila_b1 = await m.db.fetchrow("select phone, name, extra_data from public.end_customers where id = $1", b1)
    assert (fila_b1["phone"], fila_b1["name"]) == (f"+{TEL_B1}", NOMBRE_B1)
    assert (
        await m.db.fetchval(
            "select count(*) from public.notifications_log where end_customer_id = $1 and last_error is not null", b1
        )
        == 1
    )
    assert not await _pagos_sin_pagador(m, b.tenant_id)
    assert (
        await m.db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", b.tenant_id) == sesiones_b
    )
    assert await m.db.fetchval("select status from public.bookings where id = $1", b.turnos[0]) == "pending"
    datos_b = reserva.textos(await m.bot.texto(b, USUARIO_B1, "mis datos"))
    assert TEL_B1 in datos_b and NOMBRE_B1 in datos_b and TEL_C1 not in datos_b
    await verificar_aislamiento(m, "7 (tras la purga)", a, b, con_datos=True, a_activo=False)
    # el webhook del A purgado ya no resuelve a ningún tenant: 200 sin procesar ni responder
    assert await m.bot.texto(a, USUARIO_C1, "hola") == []

    # Franco sigue viendo cada tenant por separado en la impersonación de solo lectura (rol readonly).
    r = await m.admin.get(f"/tenants/{b.tenant_id}/impersonar/servicios", headers=m.cab_franco)
    assert r.status_code == 200 and {s["id"] for s in r.json()} == {str(b.servicio_id)}, r.text
    assert str(a.servicio_id) not in r.text


# --- el recorrido ------------------------------------------------------------------------------------------------


async def test_recorrido_completo_del_piloto(mundo: Mundo):
    m = mundo
    sufijo = uuid.uuid4().hex[:8]
    a = Negocio(
        etiqueta="A",
        nombre=f"Estética Aurora {sufijo}",
        slug=f"e2e-aurora-{sufijo}",
        owner_email=f"duenia-a-{sufijo}@ejemplo.invalid",
        servicio_nombre="Limpieza facial profunda",
        profesional_nombre="Lucía Aurora",
        direccion="Calle Falsa 123, Springfield",
    )
    b = Negocio(
        etiqueta="B",
        nombre=f"Peluquería Brisa {sufijo}",
        slug=f"e2e-brisa-{sufijo}",
        owner_email=f"duenia-b-{sufijo}@ejemplo.invalid",
        servicio_nombre="Manicura semipermanente",
        profesional_nombre="Marta Brisa",
        direccion="Avenida Siempreviva 742, Shelbyville",
    )

    # 1) Franco da de alta los dos tenants con su owner y carga la integración de Telegram de cada uno.
    for n in (a, b):
        await etapa_1_alta_y_telegram(m, n)
    r = await m.admin.get("/tenants", headers=m.cab_franco)
    assert {a.tenant_id, b.tenant_id} <= {uuid.UUID(t["id"]) for t in r.json()}
    await verificar_aislamiento_inicial(m, a, b)

    # 2) Cada owner carga su catálogo, disponibilidad y datos del local.
    for n in (a, b):
        await etapa_2_catalogo(m, n)
    await verificar_aislamiento(m, "2", a, b, con_datos=False)

    # 3) Un cliente reserva por el bot (webhook real) y queda pending.
    c1 = await etapa_3_reserva_por_bot(m, a, b)
    await verificar_aislamiento(m, "3", a, b, con_datos=True)

    # 4) El owner aprueba desde el dashboard: confirmed y aviso al cliente.
    await etapa_4_aprobacion(m, a, b, c1)
    await verificar_aislamiento(m, "4", a, b, con_datos=True)

    # 5) Otro pending vence y el job de vencimiento lo cancela y avisa.
    c2 = await etapa_5_vencimiento(m, a, b)
    await verificar_aislamiento(m, "5", a, b, con_datos=True)

    # 6) "Borrar mis datos" desde el bot.
    await etapa_6_borrar_mis_datos(m, a, b, c1, c2)
    await verificar_aislamiento(m, "6", a, b, con_datos=True)

    # 7) Baja del tenant, fin del período de gracia y purga.
    await etapa_7_baja_y_purga(m, a, b, c1, c2)


async def verificar_aislamiento_inicial(m: Mundo, a: Negocio, b: Negocio) -> None:
    """Recién dados de alta: cada owner entra solo a su tenant y la integración de uno no resuelve en el otro."""
    for propio, ajeno in ((a, b), (b, a)):
        r = await m.dash.get("/servicios", headers=m.cab_owner(propio))
        assert r.status_code == 200 and r.json() == [], f"[1] {propio.etiqueta}: {r.text}"
        assert propio.webhook_key != ajeno.webhook_key
        # el secret de un tenant no abre el webhook del otro
        r = await m.bot.http.post(
            propio.ruta,
            json={"update_id": 1, "message": {"message_id": 1, "from": {"id": 1}, "chat": {"id": 1}, "text": "x"}},
            headers={CABECERA_SECRET: ajeno.webhook_secret},
        )
        assert r.status_code == 401, f"[1] el secret de {ajeno.etiqueta} abrió el webhook de {propio.etiqueta}"
