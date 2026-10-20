"""Cascada de purga de tenants vencidos (ARCHITECTURE.md §2.2 y §5.4.1).

`purgar_tenants_vencidos` es la lógica reutilizable que invoca el Cron Job diario (`apps/jobs`). Corre con el
pool del rol `purge_job` (sin grants sobre tablas: solo `EXECUTE` en las funciones SECURITY DEFINER de la purga)
y usa `service_role` únicamente a través del cliente de la Auth Admin API que se le inyecta.

Por tenant, en orden:
  A. `begin_tenant_purge`: pasa el tenant de `inactive` a `purging` bajo lock, re-validando en la base que la gracia
     venció. Desde ahí nadie puede reactivarlo, suspenderlo ni darlo de baja (la carrera purga/reactivación). Es
     idempotente: sobre un tenant que ya está `purging` (purga interrumpida) no hace nada y la cascada se retoma.
     Lo audita la propia función SQL.
  0. `calendar_scrub_targets` + `limpiar_eventos`: quita nombre y teléfono de los eventos de Google Calendar
     ANTES de que el paso 1 borre las credenciales. Lo que no se pueda limpiar queda en `audit_log` como
     `calendar_scrub_failed` (solo ids y motivo) y no frena la purga.
  1. `purge_tenant_data`: la base re-valida inactive + período vencido y devuelve las cuentas de Auth a borrar.
  2. Eliminación de esas cuentas por la Auth Admin API (un 404 cuenta como éxito); las de platform admins se omiten.
  3. `mark_tenant_purged`: recién sin cuentas vinculadas setea `purged` y libera el slug.

Cada paso deja un log estructurado `tenant_purge_step` y un `audit_log` `tenant_purge_step` (los pasos A, 1 y 3 los
audita la propia función SQL; los pasos 0 y 2 ocurren fuera de la base y los audita este módulo). Todo es
idempotente: si la corrida se interrumpe en cualquier paso, la siguiente retoma y llega a `purged`. Un tenant
que falla no frena a los demás. Nada de lo que se loguea, audita o reporta lleva PII ni ids de cuentas.

Una interrupción a mitad de la cascada obliga a repetir el paso 0 en la corrida siguiente: los eventos que ya
no se pueden limpiar (credenciales ya borradas) se vuelven a registrar como `calendar_scrub_failed`.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import sentry_sdk
from platform_audit import registrar
from platform_calendar import EventoNoLimpiado, ServicioCalendario
from platform_tenancy import admin_tx, tenant_tx

from platform_admin.auth_admin import ClienteAuthEliminacion

PASO_BEGIN = "begin_tenant_purge"
PASO_CALENDAR = "calendar_scrub"
PASO_DATOS = "purge_tenant_data"
PASO_CUENTAS = "delete_auth_accounts"
PASO_MARCAR = "mark_tenant_purged"
PASOS = (PASO_BEGIN, PASO_CALENDAR, PASO_DATOS, PASO_CUENTAS, PASO_MARCAR)

PASO_LISTADO = "list_tenants_due_for_purge"
PASO_PODA = "prune_inbound_messages"

# Un tenant con más de estos días de período vencido sin llegar a `purged` se reporta a Sentry (§5.4.1).
UMBRAL_ATRASO_DIAS = 3

ESTADO_PURGED = "purged"
ESTADO_FALLIDO = "fallido"

_SQL_LISTAR = "select tenant_id, days_overdue from public.list_tenants_due_for_purge()"
_SQL_BEGIN = "select public.begin_tenant_purge($1)"
_SQL_TARGETS = "select integration_id, google_calendar_event_id from public.calendar_scrub_targets($1)"
_SQL_PURGAR = "select public.purge_tenant_data($1) as auth_user_id"
_SQL_OMITIR_ADMINS = "select public.unlink_platform_admin_accounts($1) as auth_user_id"
_SQL_MARCAR = "select public.mark_tenant_purged($1)"
_SQL_PODAR = "select public.prune_inbound_messages()"

_log = logging.getLogger("platform_admin.purga")

# Fábrica del servicio de calendario para una conexión ya abierta con el contexto del tenant que se purga:
# `get_integration_secret` exige ese contexto, así que el servicio (y su almacén de secrets) se arma sobre ella.
FabricaCalendario = Callable[[Any], ServicioCalendario]
LogPaso = Callable[..., None]


@runtime_checkable
class ReportadorErrores(Protocol):
    """Destino de las alertas (Sentry en producción; inyectable). Solo identificadores opacos, nunca PII."""

    def capturar_excepcion(self, exc: BaseException, **contexto: Any) -> None: ...

    def capturar_mensaje(self, mensaje: str, **contexto: Any) -> None: ...


class ReportadorSentry:
    """`ReportadorErrores` sobre `sentry_sdk`: el contexto va como tags del evento. Sin DSN no hace nada."""

    def capturar_excepcion(self, exc: BaseException, **contexto: Any) -> None:
        with sentry_sdk.new_scope() as scope:
            for clave, valor in contexto.items():
                scope.set_tag(clave, str(valor))
            sentry_sdk.capture_exception(exc)

    def capturar_mensaje(self, mensaje: str, **contexto: Any) -> None:
        with sentry_sdk.new_scope() as scope:
            for clave, valor in contexto.items():
                scope.set_tag(clave, str(valor))
            sentry_sdk.capture_message(mensaje, level="error")


class PasoPurgaFallido(Exception):
    """Un paso de la cascada falló para un tenant. La causa original va en `__cause__`."""

    def __init__(self, tenant_id: uuid.UUID, paso: str):
        self.tenant_id = tenant_id
        self.paso = paso
        super().__init__(f"Falló el paso {paso} de la purga")


@dataclass(frozen=True)
class ResultadoTenantPurga:
    tenant_id: uuid.UUID
    dias_vencido: int | None
    estado: str  # ESTADO_PURGED | ESTADO_FALLIDO
    paso_fallido: str | None = None


@dataclass(frozen=True)
class ResultadoPurga:
    tenants: tuple[ResultadoTenantPurga, ...]
    mensajes_podados: int | None
    error_listado: bool = False
    error_poda: bool = False

    @property
    def purgados(self) -> int:
        return sum(1 for t in self.tenants if t.estado == ESTADO_PURGED)

    @property
    def fallidos(self) -> int:
        return sum(1 for t in self.tenants if t.estado == ESTADO_FALLIDO)

    @property
    def ok(self) -> bool:
        return self.fallidos == 0 and not self.error_listado and not self.error_poda


def _log_por_defecto(evento: str, **campos: Any) -> None:
    # Mismo formato que `turnos_observabilidad.log_evento_obligatorio`; el formateador JSON de la app lo serializa.
    _log.info(evento, extra={"evento": evento, **campos})


async def _scrub_calendar(pool: Any, tenant_id: uuid.UUID, fabrica: FabricaCalendario) -> dict[str, int]:
    # Con el contexto del tenant: `get_integration_secret` lo exige (§3.2). Los fallos del calendario no lanzan.
    async with tenant_tx(pool, tenant_id) as conn:
        filas = await conn.fetch(_SQL_TARGETS, tenant_id)
        pares = [(f["integration_id"], f["google_calendar_event_id"]) for f in filas if f["integration_id"] is not None]
        fallidos: list[EventoNoLimpiado] = [
            EventoNoLimpiado(None, f["google_calendar_event_id"], "sin_integracion")  # type: ignore[arg-type]  # sin integración: integration_id es None
            for f in filas
            if f["integration_id"] is None
        ]
        if pares:
            fallidos.extend(await fabrica(conn).limpiar_eventos(pares))
        for f in fallidos:
            await registrar(
                conn,
                "calendar_scrub_failed",
                "tenants",
                tenant_id,
                {
                    "event_id": f.event_id,
                    "integration_id": None if f.integration_id is None else str(f.integration_id),
                    "motivo": f.motivo,
                },
                tenant_id=tenant_id,
            )
        resumen = {"count": len(filas), "failed": len(fallidos)}
        await registrar(
            conn, "tenant_purge_step", "tenants", tenant_id, {"step": PASO_CALENDAR, **resumen}, tenant_id=tenant_id
        )
    return resumen


async def _iniciar_purga(pool: Any, tenant_id: uuid.UUID) -> dict[str, int]:
    # Primer paso de la cascada: inactive -> purging (idempotente sobre un purging). Lo audita la función SQL.
    async with admin_tx(pool) as conn:
        await conn.fetchval(_SQL_BEGIN, tenant_id)
    return {}


async def _purgar_datos(pool: Any, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    async with admin_tx(pool) as conn:
        filas = await conn.fetch(_SQL_PURGAR, tenant_id)
    return [f["auth_user_id"] for f in filas]


async def _eliminar_cuentas(
    pool: Any, tenant_id: uuid.UUID, cuentas: list[uuid.UUID], cliente_auth: ClienteAuthEliminacion
) -> dict[str, int]:
    # La cuenta de un platform admin nunca se elimina: la base la desvincula del tenant y la devuelve para excluirla.
    async with admin_tx(pool) as conn:
        filas = await conn.fetch(_SQL_OMITIR_ADMINS, tenant_id)
    omitidas = {f["auth_user_id"] for f in filas}
    a_eliminar = [c for c in cuentas if c not in omitidas]
    # Se intentan todas aunque una falle: el progreso se conserva y la corrida siguiente termina el resto.
    primer_error: Exception | None = None
    for cuenta in a_eliminar:
        try:
            await cliente_auth.eliminar_usuario(cuenta)  # False (404, ya no existía) también es éxito
        except Exception as exc:
            if primer_error is None:
                primer_error = exc
    if primer_error is not None:
        raise primer_error
    resumen = {"count": len(a_eliminar), "omitidos": len(cuentas) - len(a_eliminar)}
    async with admin_tx(pool) as conn:
        await registrar(
            conn, "tenant_purge_step", "tenants", tenant_id, {"step": PASO_CUENTAS, **resumen}, tenant_id=tenant_id
        )
    return resumen


async def _marcar(pool: Any, tenant_id: uuid.UUID) -> dict[str, int]:
    async with admin_tx(pool) as conn:
        await conn.fetchval(_SQL_MARCAR, tenant_id)
    return {}


async def purgar_tenant(
    pool: Any,
    tenant_id: uuid.UUID,
    cliente_auth: ClienteAuthEliminacion,
    fabrica_calendario: FabricaCalendario,
    *,
    log_paso: LogPaso | None = None,
) -> None:
    """Cascada completa de un tenant. Lanza `PasoPurgaFallido` (con la causa encadenada) si un paso falla.

    El primer paso (`begin_tenant_purge`) deja el tenant en `purging` y la base re-valida inactive + período
    vencido: sobre un tenant activo falla ahí sin tocar nada. Un tenant que ya está `purging` se retoma sin error.
    """
    log = log_paso or _log_por_defecto
    cuentas: list[uuid.UUID] = []

    async def correr(paso: str, accion: Any) -> dict[str, int]:
        try:
            datos = await accion()
        except Exception as exc:
            log(
                "tenant_purge_step",
                tenant_id=str(tenant_id),
                actor_type="system",
                step=paso,
                resultado="error",
                error_tipo=type(exc).__name__,
            )
            raise PasoPurgaFallido(tenant_id, paso) from exc
        log("tenant_purge_step", tenant_id=str(tenant_id), actor_type="system", step=paso, resultado="ok", **datos)
        return datos

    await correr(PASO_BEGIN, lambda: _iniciar_purga(pool, tenant_id))
    await correr(PASO_CALENDAR, lambda: _scrub_calendar(pool, tenant_id, fabrica_calendario))

    async def paso_datos() -> dict[str, int]:
        cuentas.extend(await _purgar_datos(pool, tenant_id))
        return {"count": len(cuentas)}

    await correr(PASO_DATOS, paso_datos)
    await correr(PASO_CUENTAS, lambda: _eliminar_cuentas(pool, tenant_id, cuentas, cliente_auth))
    await correr(PASO_MARCAR, lambda: _marcar(pool, tenant_id))


async def _listar_vencidos(pool: Any) -> list[tuple[uuid.UUID, int]]:
    async with admin_tx(pool) as conn:
        filas = await conn.fetch(_SQL_LISTAR)
    return [(f["tenant_id"], f["days_overdue"]) for f in filas]


async def purgar_tenants_vencidos(
    pool: Any,
    cliente_auth: ClienteAuthEliminacion,
    fabrica_calendario: FabricaCalendario,
    *,
    reportador: ReportadorErrores,
    log_paso: LogPaso | None = None,
) -> ResultadoPurga:
    """Una corrida del Cron Job de purga (§5.4.1): purga los tenants vencidos y poda `inbound_messages`.

    No lanza por fallos de tenants ni de la poda: cada falla va al `reportador` y a los logs, y el resultado
    dice si la corrida fue limpia (`ok`) para que el job elija su código de salida. Al terminar vuelve a
    listar: todo tenant con más de `UMBRAL_ATRASO_DIAS` días vencido que sigue sin llegar a `purged` se reporta
    aunque ningún paso haya lanzado (detecta una falla silenciosa repetida).
    """
    resultados: list[ResultadoTenantPurga] = []
    error_listado = False
    vencidos: list[tuple[uuid.UUID, int]] = []
    try:
        vencidos = await _listar_vencidos(pool)
    except Exception as exc:
        error_listado = True
        reportador.capturar_excepcion(exc, paso=PASO_LISTADO)
        _log.error(
            "Falló el listado de tenants vencidos", extra={"paso": PASO_LISTADO, "error_tipo": type(exc).__name__}
        )

    for tenant_id, dias in vencidos:
        try:
            await purgar_tenant(pool, tenant_id, cliente_auth, fabrica_calendario, log_paso=log_paso)
        except PasoPurgaFallido as exc:
            reportador.capturar_excepcion(
                exc.__cause__ or exc, tenant_id=str(tenant_id), paso=exc.paso, dias_vencido=dias
            )
            resultados.append(ResultadoTenantPurga(tenant_id, dias, ESTADO_FALLIDO, exc.paso))
        else:
            resultados.append(ResultadoTenantPurga(tenant_id, dias, ESTADO_PURGED))

    if vencidos:
        await _reportar_atrasados(pool, reportador)

    mensajes_podados: int | None = None
    error_poda = False
    try:
        async with admin_tx(pool) as conn:
            mensajes_podados = await conn.fetchval(_SQL_PODAR)
    except Exception as exc:
        error_poda = True
        reportador.capturar_excepcion(exc, paso=PASO_PODA)
        _log.error("Falló la poda de inbound_messages", extra={"paso": PASO_PODA, "error_tipo": type(exc).__name__})

    return ResultadoPurga(tuple(resultados), mensajes_podados, error_listado, error_poda)


async def _reportar_atrasados(pool: Any, reportador: ReportadorErrores) -> None:
    try:
        pendientes = await _listar_vencidos(pool)
    except Exception as exc:
        reportador.capturar_excepcion(exc, paso=PASO_LISTADO)
        return
    for tenant_id, dias in pendientes:
        if dias > UMBRAL_ATRASO_DIAS:
            reportador.capturar_mensaje(
                f"Tenant con más de {UMBRAL_ATRASO_DIAS} días de período vencido sin purgar",
                tenant_id=str(tenant_id),
                dias_vencido=dias,
            )
