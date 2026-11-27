"""Cancelar, mover y resolver cambios de horario contra Postgres real, con los roles del bot y del panel.

Cubre lo que la conexión simulada no puede: el plazo leído de `tenants.settings`, el exclusion constraint y el trigger
de cambio pendiente de verdad (un choque con otro turno o con una propuesta), la auditoría con el actor del contexto y
el aislamiento entre tenants.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from platform_tenancy import crear_pool, tenant_tx

from turnos_dominio import (
    CambioNoPendiente,
    CambioVencido,
    CambioYaPendiente,
    HorarioOcupado,
    PlazoVencido,
    TurnoNoEncontrado,
    TurnoNoModificable,
    aprobar_cambio_horario,
    cancelar_turno_cliente,
    crear_turno,
    mover_turno_cliente,
    rechazar_cambio_horario,
)

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
BASE = datetime.now(UTC).replace(microsecond=0, second=0, minute=0) + timedelta(days=40)
MEDIA_HORA = timedelta(minutes=30)


def _dsn(base: str, rol: str, password: str) -> str:
    p = urlsplit(base)
    host = p.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{host}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, ""))


async def _pool(database_url, roles_password, rol):
    return await crear_pool(_dsn(database_url, rol, roles_password), min_size=1, max_size=2, statement_cache_size=0)


@pytest.fixture
async def pool_dashboard(database_url, roles_password):
    pool = await _pool(database_url, roles_password, "dashboard_service")
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def pool_bot(database_url, roles_password):
    pool = await _pool(database_url, roles_password, "bot_service")
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def escenario(db_superuser):
    """Dos tenants con cliente, servicio y profesional; el servicio de A en `auto` y el de B en `approval`."""
    sufijo = uuid.uuid4().hex[:8]
    datos: dict[str, dict[str, uuid.UUID]] = {}
    for letra, modo in (("a", "auto"), ("b", "approval")):
        d = {k: uuid.uuid4() for k in ("tenant", "cliente", "otro_cliente", "servicio", "profesional")}
        datos[letra] = d
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            d["tenant"],
            letra.upper(),
            f"it-cambios-{letra}-{sufijo}",
        )
        for cliente in (d["cliente"], d["otro_cliente"]):
            await db_superuser.execute(
                "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
                "values ($1, $2, 'telegram', $3)",
                cliente,
                d["tenant"],
                f"chat-{uuid.uuid4().hex[:8]}",
            )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) "
            "values ($1, $2, 'Corte', 30, 10)",
            d["servicio"],
            d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')",
            d["profesional"],
            d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
            "values ($1, $2, $3, $4)",
            d["tenant"],
            d["profesional"],
            d["servicio"],
            modo,
        )
    tenants = [datos["a"]["tenant"], datos["b"]["tenant"]]
    try:
        yield datos
    finally:
        await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
        await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
        await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
        for tabla in ("bookings", "professional_services", "professionals", "services", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
        await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


async def _turno(pool, d, inicio, *, estado="confirmed", vence=None, cliente=None):
    async with tenant_tx(pool, d["tenant"]) as conn:
        creado = await crear_turno(
            conn,
            tenant_id=d["tenant"],
            end_customer_id=cliente or d["cliente"],
            servicio_id=d["servicio"],
            profesional_id=d["profesional"],
            inicio=inicio,
            fin=inicio + MEDIA_HORA,
            estado=estado,
            pending_expires_at=vence,
        )
    assert creado is not None
    return creado.id


async def _fila(db, booking_id):
    return await db.fetchrow(
        "select status, lower(time_range) as inicio, upper(time_range) as fin, starts_at_propuesto, "
        "ends_at_propuesto, cambio_pendiente_expira_at from public.bookings where id = $1",
        booking_id,
    )


async def _auditoria(db, booking_id):
    return await db.fetch(
        "select actor_type, actor_id, action, metadata from public.audit_log where entity_id = $1 order by chain_seq",
        booking_id,
    )


def _canal(fila):
    metadata = fila["metadata"]
    return (json.loads(metadata) if isinstance(metadata, str) else metadata)["canal"]


async def _plazo(db, d, horas):
    await db.execute(
        "update public.tenants set settings = settings || $2::jsonb where id = $1",
        d["tenant"],
        json.dumps({"cancelacion_plazo_horas": horas}),
    )


async def _cancelar(pool, d, booking_id, canal="bot", cliente=None, tenant=None, actor=None):
    async with tenant_tx(pool, tenant or d["tenant"], actor) as conn:
        return await cancelar_turno_cliente(
            conn,
            tenant_id=tenant or d["tenant"],
            end_customer_id=cliente or d["cliente"],
            booking_id=booking_id,
            canal=canal,
        )


async def _mover(pool, d, booking_id, nuevo, canal="web", cliente=None, tenant=None, actor=None):
    async with tenant_tx(pool, tenant or d["tenant"], actor) as conn:
        return await mover_turno_cliente(
            conn,
            tenant_id=tenant or d["tenant"],
            end_customer_id=cliente or d["cliente"],
            booking_id=booking_id,
            nuevo_inicio=nuevo,
            canal=canal,
        )


# --- cancelar ------------------------------------------------------------------------------------------------------


async def test_cancelar_dentro_del_plazo_cancela_y_audita_con_el_actor_del_contexto(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, BASE)
    actor = uuid.uuid4()
    await _cancelar(pool_bot, a, reserva, canal="bot", actor=actor)
    assert (await _fila(db_superuser, reserva))["status"] == "cancelled"
    (fila,) = await _auditoria(db_superuser, reserva)
    assert (fila["actor_type"], fila["actor_id"], fila["action"]) == ("bot", actor, "booking_cancelled_by_customer")
    assert _canal(fila) == "bot"


async def test_cancelar_fuera_del_plazo_se_rechaza_sin_tocar_nada(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, datetime.now(UTC) + timedelta(hours=23, minutes=50))
    with pytest.raises(PlazoVencido) as error:
        await _cancelar(pool_bot, a, reserva)
    assert error.value.horas_plazo == 24
    assert (await _fila(db_superuser, reserva))["status"] == "confirmed"
    assert await _auditoria(db_superuser, reserva) == []


async def test_el_plazo_configurado_por_el_negocio_manda(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, datetime.now(UTC) + timedelta(hours=5))
    await _plazo(db_superuser, a, 6)
    with pytest.raises(PlazoVencido) as error:
        await _cancelar(pool_bot, a, reserva)
    assert error.value.horas_plazo == 6
    await _plazo(db_superuser, a, 4)
    await _cancelar(pool_bot, a, reserva)
    assert (await _fila(db_superuser, reserva))["status"] == "cancelled"


async def test_con_plazo_cero_se_cancela_hasta_el_inicio_y_no_un_turno_que_ya_empezo(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    await _plazo(db_superuser, a, 0)
    proximo = await _turno(pool_bot, a, datetime.now(UTC) + timedelta(minutes=5))
    await _cancelar(pool_bot, a, proximo)
    empezado = await db_superuser.fetchval(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
        "values ($1, $2, $3, $4, tstzrange(now() - interval '5 minutes', now() + interval '25 minutes'), 'confirmed') "
        "returning id",
        a["tenant"],
        a["cliente"],
        a["servicio"],
        a["profesional"],
    )
    with pytest.raises(PlazoVencido) as error:
        await _cancelar(pool_bot, a, empezado)
    assert error.value.horas_plazo == 0


async def test_cancelar_un_pending_dentro_del_plazo_y_un_turno_ya_cancelado(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, BASE, estado="pending", vence=datetime.now(UTC) + timedelta(hours=2))
    await _cancelar(pool_bot, a, reserva)
    assert (await _fila(db_superuser, reserva))["status"] == "cancelled"
    with pytest.raises(TurnoNoModificable):
        await _cancelar(pool_bot, a, reserva)


async def test_cancelar_un_turno_confirmado_con_cambio_pendiente_descarta_la_propuesta(
    pool_dashboard, escenario, db_superuser
):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=3))
    await _cancelar(pool_dashboard, b, reserva, canal="web")
    fila = await _fila(db_superuser, reserva)
    assert fila["status"] == "cancelled" and fila["starts_at_propuesto"] is None


async def test_no_se_cancela_ni_se_mueve_un_turno_de_otro_cliente_ni_de_otro_tenant(pool_bot, escenario, db_superuser):
    a, b = escenario["a"], escenario["b"]
    reserva = await _turno(pool_bot, a, BASE)
    with pytest.raises(TurnoNoEncontrado):
        await _cancelar(pool_bot, a, reserva, cliente=a["otro_cliente"])
    with pytest.raises(TurnoNoEncontrado):
        await _cancelar(pool_bot, a, reserva, tenant=b["tenant"], cliente=b["cliente"])
    with pytest.raises(TurnoNoEncontrado):
        await _mover(pool_bot, a, reserva, BASE + timedelta(hours=3), cliente=a["otro_cliente"])
    with pytest.raises(TurnoNoEncontrado):
        await _mover(pool_bot, a, reserva, BASE + timedelta(hours=3), tenant=b["tenant"], cliente=b["cliente"])
    fila = await _fila(db_superuser, reserva)
    assert (fila["status"], fila["inicio"], fila["starts_at_propuesto"]) == ("confirmed", BASE, None)
    assert await _auditoria(db_superuser, reserva) == []


async def test_el_cliente_del_otro_tenant_no_resuelve_ni_aprueba_cambios_ajenos(pool_dashboard, escenario):
    a, b = escenario["a"], escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=3))
    for funcion in (aprobar_cambio_horario, rechazar_cambio_horario):
        with pytest.raises(TurnoNoEncontrado):
            async with tenant_tx(pool_dashboard, a["tenant"]) as conn:
                await funcion(conn, tenant_id=a["tenant"], booking_id=reserva)


# --- mover ---------------------------------------------------------------------------------------------------------


async def test_mover_en_modo_auto_mueve_directo_y_audita_como_cliente_por_web(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, BASE)
    nuevo = BASE + timedelta(hours=3)
    movido = await _mover(pool_bot, a, reserva, nuevo, canal="web")
    assert movido.resultado == "movido"
    fila = await _fila(db_superuser, reserva)
    assert (fila["inicio"], fila["fin"], fila["starts_at_propuesto"]) == (nuevo, nuevo + MEDIA_HORA, None)
    (auditoria,) = await _auditoria(db_superuser, reserva)
    assert auditoria["action"] == "booking_moved_by_customer" and _canal(auditoria) == "web"


async def test_mover_fuera_del_plazo_se_rechaza(pool_bot, escenario, db_superuser):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, datetime.now(UTC) + timedelta(hours=2))
    with pytest.raises(PlazoVencido):
        await _mover(pool_bot, a, reserva, BASE)
    assert (await _fila(db_superuser, reserva))["fin"] - (await _fila(db_superuser, reserva))["inicio"] == MEDIA_HORA
    assert await _auditoria(db_superuser, reserva) == []


async def test_mover_a_un_hueco_ocupado_es_horario_ocupado_y_la_transaccion_sigue_viva(
    pool_bot, escenario, db_superuser
):
    a = escenario["a"]
    reserva = await _turno(pool_bot, a, BASE)
    await _turno(pool_bot, a, BASE + timedelta(hours=3), cliente=a["otro_cliente"])
    async with tenant_tx(pool_bot, a["tenant"]) as conn:
        with pytest.raises(HorarioOcupado):
            await mover_turno_cliente(
                conn,
                tenant_id=a["tenant"],
                end_customer_id=a["cliente"],
                booking_id=reserva,
                nuevo_inicio=BASE + timedelta(hours=3, minutes=15),
                canal="bot",
            )
        assert await conn.fetchval("select 1") == 1
    assert (await _fila(db_superuser, reserva))["inicio"] == BASE


async def test_mover_en_modo_approval_deja_el_original_reservado_y_guarda_la_propuesta(
    pool_dashboard, escenario, db_superuser
):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    nuevo = BASE + timedelta(hours=3)
    movido = await _mover(pool_dashboard, b, reserva, nuevo)
    assert movido.resultado == "pendiente_de_aprobacion"
    fila = await _fila(db_superuser, reserva)
    assert (fila["status"], fila["inicio"], fila["fin"]) == ("confirmed", BASE, BASE + MEDIA_HORA)
    assert (fila["starts_at_propuesto"], fila["ends_at_propuesto"]) == (nuevo, nuevo + MEDIA_HORA)
    assert fila["cambio_pendiente_expira_at"] > datetime.now(UTC)
    # el original sigue ocupado y el propuesto también
    with pytest.raises(HorarioOcupado):
        await _mover(pool_dashboard, b, await _turno(pool_dashboard, b, BASE + timedelta(hours=6)), BASE)
    assert await crear_turno_directo(pool_dashboard, b, nuevo + timedelta(minutes=10)) is None
    (auditoria,) = await _auditoria(db_superuser, reserva)
    assert auditoria["action"] == "booking_move_requested"


async def crear_turno_directo(pool, d, inicio):
    async with tenant_tx(pool, d["tenant"]) as conn:
        return await crear_turno(
            conn,
            tenant_id=d["tenant"],
            end_customer_id=d["otro_cliente"],
            servicio_id=d["servicio"],
            profesional_id=d["profesional"],
            inicio=inicio,
            fin=inicio + MEDIA_HORA,
            estado="confirmed",
        )


async def test_proponer_sobre_la_propuesta_de_otro_turno_es_horario_ocupado(pool_dashboard, escenario):
    b = escenario["b"]
    primero = await _turno(pool_dashboard, b, BASE)
    segundo = await _turno(pool_dashboard, b, BASE + timedelta(hours=6), cliente=b["otro_cliente"])
    nuevo = BASE + timedelta(hours=3)
    await _mover(pool_dashboard, b, primero, nuevo)
    with pytest.raises(HorarioOcupado):
        await _mover(pool_dashboard, b, segundo, nuevo + timedelta(minutes=15), cliente=b["otro_cliente"])


async def test_no_se_propone_un_segundo_cambio_mientras_el_primero_espera(pool_dashboard, escenario):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=3))
    with pytest.raises(CambioYaPendiente):
        await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=5))


async def test_mover_un_pending_es_directo_y_acorta_el_vencimiento(pool_dashboard, escenario, db_superuser):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE, estado="pending", vence=BASE + timedelta(hours=1))
    nuevo = BASE - timedelta(hours=3)
    movido = await _mover(pool_dashboard, b, reserva, nuevo)
    assert movido.resultado == "movido"
    fila = await db_superuser.fetchrow(
        "select lower(time_range) as inicio, pending_expires_at from public.bookings where id = $1", reserva
    )
    assert (fila["inicio"], fila["pending_expires_at"]) == (nuevo, nuevo)


# --- aprobar, rechazar y vencer ------------------------------------------------------------------------------------


async def test_aprobar_el_cambio_mueve_el_turno_y_audita_como_staff(pool_dashboard, escenario, db_superuser):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    nuevo = BASE + timedelta(hours=3)
    await _mover(pool_dashboard, b, reserva, nuevo)
    actor = uuid.uuid4()
    async with tenant_tx(pool_dashboard, b["tenant"], actor) as conn:
        resuelto = await aprobar_cambio_horario(conn, tenant_id=b["tenant"], booking_id=reserva)
    assert resuelto.aprobado
    fila = await _fila(db_superuser, reserva)
    assert (fila["inicio"], fila["fin"], fila["starts_at_propuesto"]) == (nuevo, nuevo + MEDIA_HORA, None)
    ultima = (await _auditoria(db_superuser, reserva))[-1]
    assert (ultima["actor_type"], ultima["actor_id"], ultima["action"]) == ("staff", actor, "booking_change_approved")
    assert _canal(ultima) == "staff"
    async with tenant_tx(pool_dashboard, b["tenant"]) as conn:
        with pytest.raises(CambioNoPendiente):
            await aprobar_cambio_horario(conn, tenant_id=b["tenant"], booking_id=reserva)


async def test_rechazar_el_cambio_conserva_el_original_y_libera_el_propuesto(pool_dashboard, escenario, db_superuser):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    nuevo = BASE + timedelta(hours=3)
    await _mover(pool_dashboard, b, reserva, nuevo)
    async with tenant_tx(pool_dashboard, b["tenant"]) as conn:
        resuelto = await rechazar_cambio_horario(conn, tenant_id=b["tenant"], booking_id=reserva)
    assert not resuelto.aprobado
    fila = await _fila(db_superuser, reserva)
    assert (fila["inicio"], fila["starts_at_propuesto"]) == (BASE, None)
    assert await crear_turno_directo(pool_dashboard, b, nuevo) is not None
    assert (await _auditoria(db_superuser, reserva))[-1]["action"] == "booking_change_rejected"


async def test_un_cambio_vencido_no_se_aprueba_conserva_el_original_y_ya_no_bloquea(
    pool_dashboard, escenario, db_superuser
):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    nuevo = BASE + timedelta(hours=3)
    await _mover(pool_dashboard, b, reserva, nuevo)
    await db_superuser.execute(
        "update public.bookings set cambio_pendiente_expira_at = now() - interval '1 minute' where id = $1", reserva
    )
    async with tenant_tx(pool_dashboard, b["tenant"]) as conn:
        with pytest.raises(CambioVencido):
            await aprobar_cambio_horario(conn, tenant_id=b["tenant"], booking_id=reserva)
    assert (await _fila(db_superuser, reserva))["inicio"] == BASE
    assert await crear_turno_directo(pool_dashboard, b, nuevo) is not None  # el vencido no bloquea la agenda
    async with tenant_tx(pool_dashboard, b["tenant"]) as conn:  # y rechazarlo sigue siendo posible
        await rechazar_cambio_horario(conn, tenant_id=b["tenant"], booking_id=reserva)
    assert (await _fila(db_superuser, reserva))["starts_at_propuesto"] is None


async def test_el_job_de_vencimiento_conserva_el_original_y_se_puede_volver_a_mover(
    pool_dashboard, escenario, db_superuser
):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=3))
    await db_superuser.execute(
        "update public.bookings set cambio_pendiente_expira_at = now() - interval '1 minute' where id = $1", reserva
    )
    await db_superuser.fetch("select * from public.revert_expired_booking_changes()")
    fila = await _fila(db_superuser, reserva)
    assert (fila["inicio"], fila["starts_at_propuesto"]) == (BASE, None)
    movido = await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=5))
    assert movido.resultado == "pendiente_de_aprobacion"


async def test_mover_con_un_cambio_vencido_sin_revertir_lo_descarta_y_propone_el_nuevo(
    pool_dashboard, escenario, db_superuser
):
    b = escenario["b"]
    reserva = await _turno(pool_dashboard, b, BASE)
    await _mover(pool_dashboard, b, reserva, BASE + timedelta(hours=3))
    await db_superuser.execute(
        "update public.bookings set cambio_pendiente_expira_at = now() - interval '1 minute' where id = $1", reserva
    )
    nuevo = BASE + timedelta(hours=5)
    await _mover(pool_dashboard, b, reserva, nuevo)
    fila = await _fila(db_superuser, reserva)
    assert (fila["inicio"], fila["starts_at_propuesto"]) == (BASE, nuevo)
