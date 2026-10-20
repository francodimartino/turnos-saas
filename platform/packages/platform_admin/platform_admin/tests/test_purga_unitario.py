"""Cascada de purga (§2.2, §5.4.1) con una base simulada con estado, Auth y Calendar falsos y Sentry falso.

La base simulada replica el contrato de las funciones SQL (ya probadas contra Postgres real): `purge_tenant_data`
devuelve las cuentas que todavía están vinculadas, `mark_tenant_purged` rechaza si quedan cuentas, y un tenant
`purged` o no vencido sale de `list_tenants_due_for_purge`.
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

import pytest
from platform_calendar import EventoNoLimpiado, TextoNeutro

from platform_admin import (
    ErrorEliminacionCuenta,
    PasoPurgaFallido,
    ReportadorErrores,
    purgar_tenant,
    purgar_tenants_vencidos,
)
from platform_admin.purga import PASOS

TEXTO_NEUTRO = TextoNeutro("Evento anonimizado", "Sin datos del cliente.")

T1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
T2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
T_ACTIVO = uuid.UUID("33333333-3333-3333-3333-333333333333")
INT = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _cuenta(n):
    return uuid.UUID(int=n + 1000)


class BaseSimulada:
    """Una conexión que hace de purge_job: solo entiende las llamadas a funciones SQL de la purga."""

    def __init__(self):
        # tenant_id -> {"status", "dias", "cuentas": set}
        self.tenants = {
            T1: {"status": "inactive", "dias": 10, "cuentas": {_cuenta(1), _cuenta(2)}},
            T_ACTIVO: {"status": "active", "dias": None, "cuentas": {_cuenta(3)}},
        }
        self.eventos = {T1: [(INT, "ev-1"), (None, "ev-2")]}
        self.inyectar: dict[str, list[BaseException]] = {}
        self.llamadas: list[str] = []
        self.auditorias: list[tuple] = []
        self.contextos: list[tuple] = []
        self.podados = 7
        self.mark_no_avanza = False
        self.platform_admins: set = set()  # cuentas que figuran en platform_admins

    def _fallar(self, clave):
        pendientes = self.inyectar.get(clave)
        if pendientes:
            raise pendientes.pop(0)

    def _vencidos(self):
        # como list_tenants_due_for_purge: inactive vencidos y purging (purga a retomar)
        return [
            (t, d["dias"])
            for t, d in self.tenants.items()
            if d["status"] in ("inactive", "purging") and d["dias"] is not None
        ]

    def _exigir_vencido(self, tenant_id):
        if tenant_id not in dict(self._vencidos()):
            raise RuntimeError("purga rechazada")

    async def execute(self, sql, *args):
        if "set_config" in sql:
            self.contextos.append(args)
        return "OK"

    async def fetch(self, sql, *args):
        if "list_tenants_due_for_purge" in sql:
            self.llamadas.append("list")
            self._fallar("list")
            return [{"tenant_id": t, "days_overdue": d} for t, d in self._vencidos()]
        if "calendar_scrub_targets" in sql:
            self.llamadas.append("calendar_scrub_targets")
            self._fallar("calendar_scrub_targets")
            self._exigir_vencido(args[0])
            return [{"integration_id": i, "google_calendar_event_id": e} for i, e in self.eventos.get(args[0], [])]
        if "purge_tenant_data" in sql:
            self.llamadas.append("purge_tenant_data")
            self._fallar("purge_tenant_data")
            self._exigir_vencido(args[0])
            return [{"auth_user_id": c} for c in sorted(self.tenants[args[0]]["cuentas"])]
        if "unlink_platform_admin_accounts" in sql:
            self.llamadas.append("unlink_platform_admin_accounts")
            self._fallar("unlink_platform_admin_accounts")
            datos = self.tenants[args[0]]
            admins = sorted(datos["cuentas"] & self.platform_admins)
            datos["cuentas"] -= set(admins)  # desvincula (auth_user_id a null)
            return [{"auth_user_id": c} for c in admins]
        raise AssertionError(f"SQL inesperado: {sql}")

    async def fetchval(self, sql, *args):
        if "begin_tenant_purge" in sql:
            self.llamadas.append("begin_tenant_purge")
            self._fallar("begin_tenant_purge")
            self._exigir_vencido(args[0])  # active, purged o no vencido: rechazado
            self.tenants[args[0]]["status"] = "purging"  # idempotente si ya lo estaba
            return None
        if "mark_tenant_purged" in sql:
            self.llamadas.append("mark_tenant_purged")
            self._fallar("mark_tenant_purged")
            datos = self.tenants[args[0]]
            if datos["status"] == "purged" or self.mark_no_avanza:
                return "slug"
            self._exigir_vencido(args[0])
            if datos["cuentas"]:
                raise RuntimeError("quedan cuentas vinculadas")
            datos["status"] = "purged"
            return "slug-purged"
        if "prune_inbound_messages" in sql:
            self.llamadas.append("prune_inbound_messages")
            self._fallar("prune")
            return self.podados
        if "write_audit_log" in sql:
            self.llamadas.append("write_audit_log")
            self.auditorias.append(args)
            return uuid.uuid4()
        raise AssertionError(f"SQL inesperado: {sql}")


class PoolSimulado:
    def __init__(self, base):
        self.base = base

    @asynccontextmanager
    async def acquire(self):
        yield self.base


class AuthFalso:
    """Auth Admin API falsa: borra la cuenta de la base simulada (como el `on delete set null` real)."""

    def __init__(self, base):
        self.base = base
        self.pedidos: list[uuid.UUID] = []
        self.inyectar: list[BaseException] = []
        self.ya_inexistentes: set[uuid.UUID] = set()

    async def eliminar_usuario(self, auth_user_id):
        self.pedidos.append(auth_user_id)
        if self.inyectar:
            raise self.inyectar.pop(0)
        for datos in self.base.tenants.values():
            datos["cuentas"].discard(auth_user_id)
        return auth_user_id not in self.ya_inexistentes


class CalendarioFalso:
    def __init__(self):
        self.pares: list = []
        self.textos: list = []
        self.fallidos: list = []
        self.conexiones: list = []

    def para(self, conn):
        self.conexiones.append(conn)
        return self

    async def anonimizar_eventos(self, pares, texto):
        self.pares.append(list(pares))
        self.textos.append(texto)
        return list(self.fallidos)


class ReportadorFalso:
    def __init__(self):
        self.excepciones: list[tuple] = []
        self.mensajes: list[tuple] = []

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))

    def capturar_mensaje(self, mensaje, **contexto):
        self.mensajes.append((mensaje, contexto))


class LogFalso:
    def __init__(self):
        self.eventos: list[tuple] = []

    def __call__(self, evento, **campos):
        self.eventos.append((evento, campos))


class Entorno:
    def __init__(self):
        self.base = BaseSimulada()
        self.pool = PoolSimulado(self.base)
        self.auth = AuthFalso(self.base)
        self.calendario = CalendarioFalso()
        self.reportador = ReportadorFalso()
        self.log = LogFalso()

    async def correr(self):
        return await purgar_tenants_vencidos(
            self.pool,
            self.auth,
            self.calendario.para,
            texto_neutro=TEXTO_NEUTRO,
            reportador=self.reportador,
            log_paso=self.log,
        )


@pytest.fixture
def e():
    return Entorno()


def _audit(base, accion="tenant_purge_step"):
    return [(a[1], json.loads(a[3]) if a[3] else None) for a in base.auditorias if a[0] == accion]


# ---------------------------------------------------------------------------
# Orden y contenido de los pasos
# ---------------------------------------------------------------------------


async def test_ejecuta_los_pasos_en_orden_con_begin_primero_y_llega_a_purged(e):
    resultado = await e.correr()
    pasos = [
        x
        for x in e.base.llamadas
        if x in {"begin_tenant_purge", "calendar_scrub_targets", "purge_tenant_data", "mark_tenant_purged"}
    ]
    assert pasos == ["begin_tenant_purge", "calendar_scrub_targets", "purge_tenant_data", "mark_tenant_purged"]
    assert e.base.tenants[T1]["status"] == "purged"
    assert [t.estado for t in resultado.tenants] == ["purged"]
    assert resultado.ok and resultado.purgados == 1 and resultado.fallidos == 0
    assert sorted(e.auth.pedidos) == [_cuenta(1), _cuenta(2)]
    assert PASOS == (
        "begin_tenant_purge",
        "calendar_scrub",
        "purge_tenant_data",
        "delete_auth_accounts",
        "mark_tenant_purged",
    )


async def test_begin_tenant_purge_es_el_primer_paso_y_deja_el_tenant_en_purging_antes_del_scrub(e):
    await e.correr()
    assert e.base.llamadas.index("begin_tenant_purge") < e.base.llamadas.index("calendar_scrub_targets")
    primero = next(c for ev, c in e.log.eventos if ev == "tenant_purge_step")
    assert primero["step"] == "begin_tenant_purge" and primero["resultado"] == "ok"


async def test_un_tenant_que_ya_esta_en_purging_se_retoma_sin_error(e):
    e.base.tenants[T1]["status"] = "purging"  # purga interrumpida en una corrida anterior
    resultado = await e.correr()
    assert resultado.ok and [t.estado for t in resultado.tenants] == ["purged"]
    assert e.base.tenants[T1]["status"] == "purged"
    assert e.base.llamadas.count("begin_tenant_purge") == 1


async def test_si_begin_falla_no_se_toca_nada_mas_y_el_paso_fallido_es_begin(e):
    e.base.inyectar["begin_tenant_purge"] = [RuntimeError("fallo")]
    resultado = await e.correr()
    assert resultado.tenants[0].paso_fallido == "begin_tenant_purge"
    assert "calendar_scrub_targets" not in e.base.llamadas and e.base.tenants[T1]["status"] == "inactive"


async def test_paso_0_limpia_los_eventos_antes_de_borrar_credenciales(e):
    await e.correr()
    assert e.calendario.pares == [[(INT, "ev-1")]]  # el de integration_id null no se puede limpiar
    assert e.base.llamadas.index("calendar_scrub_targets") < e.base.llamadas.index("purge_tenant_data")


async def test_paso_0_corre_con_el_contexto_del_tenant_que_se_purga(e):
    await e.correr()
    assert any(str(T1) in args for args in e.base.contextos)
    assert e.calendario.conexiones == [e.base]


async def test_los_eventos_se_limpian_con_el_texto_neutro_que_decide_la_app(e):
    await e.correr()
    assert e.calendario.textos and all(t == TEXTO_NEUTRO for t in e.calendario.textos)


async def test_eventos_no_limpiados_van_a_audit_como_calendar_scrub_failed_sin_pii(e):
    e.calendario.fallidos = [EventoNoLimpiado(INT, "ev-1", "token_vencido")]
    await e.correr()
    fallos = _audit(e.base, "calendar_scrub_failed")
    assert sorted((m["event_id"], m["motivo"]) for _, m in fallos) == [
        ("ev-1", "token_vencido"),
        ("ev-2", "sin_integracion"),
    ]
    assert all(entidad == "tenants" for entidad, _ in fallos)
    assert e.base.tenants[T1]["status"] == "purged"  # un evento no limpiado no frena la purga


async def test_cada_paso_deja_audit_tenant_purge_step_y_log_estructurado(e):
    await e.correr()
    por_paso = {c["step"]: c for ev, c in e.log.eventos if ev == "tenant_purge_step"}
    assert set(por_paso) == set(PASOS)
    for campos in por_paso.values():
        assert campos["tenant_id"] == str(T1) and campos["actor_type"] == "system" and campos["resultado"] == "ok"
    assert por_paso["delete_auth_accounts"]["count"] == 2
    # audit de los pasos que ocurren fuera de las funciones SQL (las SQL ya auditan el 1 y el 3)
    audit = {m["step"]: m for _, m in _audit(e.base)}
    assert set(audit) == {"calendar_scrub", "delete_auth_accounts"}
    assert audit["delete_auth_accounts"]["count"] == 2
    assert audit["calendar_scrub"]["count"] == 2


async def test_logs_y_audit_no_llevan_ids_de_cuenta(e):
    await e.correr()
    texto = repr(e.log.eventos) + repr(e.base.auditorias)
    for cuenta in (_cuenta(1), _cuenta(2)):
        assert str(cuenta) not in texto


async def test_un_404_de_auth_cuenta_como_exito(e):
    e.auth.ya_inexistentes = {_cuenta(1), _cuenta(2)}
    resultado = await e.correr()
    assert resultado.ok and e.base.tenants[T1]["status"] == "purged"


async def test_log_por_defecto_usa_logging_estandar(e, caplog):
    with caplog.at_level(logging.INFO):
        await purgar_tenants_vencidos(
            e.pool, e.auth, e.calendario.para, texto_neutro=TEXTO_NEUTRO, reportador=e.reportador
        )
    registros = [r for r in caplog.records if getattr(r, "evento", None) == "tenant_purge_step"]
    assert len(registros) == len(PASOS)
    assert registros[0].tenant_id == str(T1)


# ---------------------------------------------------------------------------
# Lo que no se toca
# ---------------------------------------------------------------------------


async def test_un_tenant_active_o_sin_vencer_nunca_se_toca(e):
    e.base.tenants[T2] = {"status": "inactive", "dias": None, "cuentas": {_cuenta(9)}}  # período sin vencer
    await e.correr()
    assert e.base.tenants[T_ACTIVO]["status"] == "active" and e.base.tenants[T2]["status"] == "inactive"
    assert _cuenta(3) not in e.auth.pedidos and _cuenta(9) not in e.auth.pedidos
    assert all(str(T_ACTIVO) not in repr(a) and str(T2) not in repr(a) for a in e.base.auditorias)


async def test_purgar_tenant_directo_sobre_un_activo_falla_en_begin_sin_borrar_nada(e):
    with pytest.raises(PasoPurgaFallido) as exc:
        await purgar_tenant(e.pool, T_ACTIVO, e.auth, e.calendario.para, texto_neutro=TEXTO_NEUTRO, log_paso=e.log)
    assert exc.value.paso == "begin_tenant_purge" and exc.value.tenant_id == T_ACTIVO
    assert "calendar_scrub_targets" not in e.base.llamadas
    assert e.auth.pedidos == [] and e.base.tenants[T_ACTIVO]["status"] == "active"


async def test_tenant_ya_purged_no_se_reprocesa(e):
    await e.correr()
    antes = len(e.base.llamadas)
    resultado = await e.correr()
    assert resultado.tenants == ()
    assert "calendar_scrub_targets" not in e.base.llamadas[antes:]


# ---------------------------------------------------------------------------
# Idempotencia: una excepción en cada paso y la corrida siguiente retoma
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("paso", PASOS)
async def test_interrumpida_en_cada_paso_la_corrida_siguiente_llega_a_purged(e, paso):
    boom = RuntimeError("fallo transitorio")
    if paso == "begin_tenant_purge":
        e.base.inyectar["begin_tenant_purge"] = [boom]
    elif paso == "calendar_scrub":
        e.base.inyectar["calendar_scrub_targets"] = [boom]
    elif paso == "purge_tenant_data":
        e.base.inyectar["purge_tenant_data"] = [boom]
    elif paso == "delete_auth_accounts":
        e.auth.inyectar = [ErrorEliminacionCuenta("Auth Admin API respondió 500")]
    else:
        e.base.inyectar["mark_tenant_purged"] = [boom]

    primera = await e.correr()
    assert not primera.ok and primera.tenants[0].estado == "fallido" and primera.tenants[0].paso_fallido == paso
    # interrumpida después de begin queda en purging (no reactivable); si falló begin sigue inactive
    assert e.base.tenants[T1]["status"] == ("inactive" if paso == "begin_tenant_purge" else "purging")

    segunda = await e.correr()  # el día siguiente, sin ninguna excepción
    assert segunda.ok and segunda.tenants[0].estado == "purged"
    assert e.base.tenants[T1]["status"] == "purged" and not e.base.tenants[T1]["cuentas"]


async def test_si_falla_la_eliminacion_de_una_cuenta_se_intentan_las_demas_y_no_se_marca_purged(e):
    e.auth.inyectar = [ErrorEliminacionCuenta("Auth Admin API respondió 500")]
    resultado = await e.correr()
    assert len(e.auth.pedidos) == 2  # la segunda cuenta se intentó igual
    assert resultado.tenants[0].paso_fallido == "delete_auth_accounts"
    assert "mark_tenant_purged" not in e.base.llamadas and e.base.tenants[T1]["status"] == "purging"


async def test_un_tenant_que_falla_no_frena_a_los_demas(e):
    e.base.tenants[T2] = {"status": "inactive", "dias": 5, "cuentas": set()}
    e.base.inyectar["purge_tenant_data"] = [RuntimeError("falla del primero")]
    resultado = await e.correr()
    assert [t.estado for t in resultado.tenants] == ["fallido", "purged"]
    assert e.base.tenants[T2]["status"] == "purged"


# ---------------------------------------------------------------------------
# Sentry y poda
# ---------------------------------------------------------------------------


async def test_cada_falla_de_paso_se_reporta_con_tenant_y_paso(e):
    e.base.inyectar["purge_tenant_data"] = [RuntimeError("fallo")]
    await e.correr()
    ((exc, contexto),) = e.reportador.excepciones
    assert isinstance(exc, RuntimeError)
    assert contexto["tenant_id"] == str(T1) and contexto["paso"] == "purge_tenant_data"


async def test_tenant_con_mas_de_3_dias_vencido_sin_purgar_se_reporta_aunque_no_haya_excepcion(e):
    # Falla silenciosa: ningún paso lanza, pero el tenant sigue vencido porque mark_tenant_purged no avanzó.
    e.base.tenants[T1]["dias"] = 4
    e.base.mark_no_avanza = True
    await e.correr()
    assert e.reportador.excepciones == []
    ((_, contexto),) = e.reportador.mensajes
    assert contexto["tenant_id"] == str(T1) and contexto["dias_vencido"] == 4


async def test_tenant_vencido_hace_exactamente_3_dias_no_se_reporta_por_atraso(e):
    e.base.tenants[T1]["dias"] = 3
    e.base.inyectar["purge_tenant_data"] = [RuntimeError("fallo")]
    await e.correr()
    assert e.reportador.mensajes == [] and len(e.reportador.excepciones) == 1


async def test_tenant_con_mas_de_3_dias_que_falla_se_reporta_la_falla_y_el_atraso(e):
    e.base.tenants[T1]["dias"] = 12
    e.base.inyectar["mark_tenant_purged"] = [RuntimeError("fallo")]
    await e.correr()
    assert len(e.reportador.excepciones) == 1
    assert [c["tenant_id"] for _, c in e.reportador.mensajes] == [str(T1)]


async def test_tenant_con_mas_de_3_dias_que_se_purga_en_la_corrida_no_se_reporta(e):
    e.base.tenants[T1]["dias"] = 40
    await e.correr()
    assert e.reportador.mensajes == [] and e.reportador.excepciones == []


async def test_invoca_prune_inbound_messages_en_la_misma_corrida(e):
    resultado = await e.correr()
    assert "prune_inbound_messages" in e.base.llamadas and resultado.mensajes_podados == 7


async def test_prune_corre_aunque_no_haya_tenants_vencidos(e):
    del e.base.tenants[T1]
    resultado = await e.correr()
    assert resultado.tenants == () and resultado.mensajes_podados == 7 and resultado.ok


async def test_si_falla_el_listado_se_reporta_y_igual_se_poda(e):
    e.base.inyectar["list"] = [RuntimeError("base caída")]
    resultado = await e.correr()
    assert not resultado.ok and resultado.error_listado and resultado.mensajes_podados == 7
    assert e.reportador.excepciones[0][1]["paso"] == "list_tenants_due_for_purge"


async def test_si_falla_la_poda_se_reporta_sin_romper_la_purga(e):
    e.base.inyectar["prune"] = [RuntimeError("fallo")]
    resultado = await e.correr()
    assert not resultado.ok and resultado.error_poda and resultado.purgados == 1
    assert e.reportador.excepciones[0][1]["paso"] == "prune_inbound_messages"


# ---------------------------------------------------------------------------
# Adaptador de Sentry
# ---------------------------------------------------------------------------


def test_reportador_sentry_cumple_el_protocolo():
    from platform_admin import ReportadorSentry

    assert isinstance(ReportadorSentry(), ReportadorErrores)


def test_reportador_sentry_envia_excepcion_y_mensaje_con_tags_sin_pii():
    import sentry_sdk
    from sentry_sdk.transport import Transport

    from platform_admin import ReportadorSentry

    eventos: list[dict] = []

    class TransporteFalso(Transport):
        def capture_envelope(self, envelope):
            if envelope.get_event() is not None:
                eventos.append(envelope.get_event())

    sentry_sdk.init(
        dsn="https://clave@o0.ingest.example.test/1", transport=TransporteFalso(), default_integrations=False
    )
    try:
        reportador = ReportadorSentry()
        reportador.capturar_excepcion(RuntimeError("fallo"), tenant_id=str(T1), paso="purge_tenant_data")
        reportador.capturar_mensaje("Tenant vencido sin purgar", tenant_id=str(T1), dias_vencido=5)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # deja el SDK desactivado
    assert len(eventos) == 2
    assert eventos[0]["tags"] == {"tenant_id": str(T1), "paso": "purge_tenant_data"}
    assert eventos[1]["message"] == "Tenant vencido sin purgar" and eventos[1]["tags"]["dias_vencido"] == "5"


async def test_la_cuenta_de_un_platform_admin_no_se_elimina_en_auth_y_se_cuenta_como_omitida(e):
    e.base.platform_admins = {_cuenta(1)}
    resultado = await e.correr()
    assert resultado.ok and e.base.tenants[T1]["status"] == "purged"
    assert e.auth.pedidos == [_cuenta(2)]  # solo la cuenta común
    paso = [ev for ev in e.log.eventos if ev[1].get("step") == "delete_auth_accounts"]
    assert paso and paso[0][1]["omitidos"] == 1 and paso[0][1]["count"] == 1
    assert str(_cuenta(1)) not in repr(e.log.eventos) + repr(e.base.auditorias)


async def test_si_todas_las_cuentas_son_de_platform_admins_no_se_llama_a_auth(e):
    e.base.platform_admins = {_cuenta(1), _cuenta(2)}
    resultado = await e.correr()
    assert resultado.ok and e.base.tenants[T1]["status"] == "purged" and e.auth.pedidos == []


async def test_sin_platform_admins_se_eliminan_todas_las_cuentas(e):
    await e.correr()
    assert sorted(e.auth.pedidos) == [_cuenta(1), _cuenta(2)]
