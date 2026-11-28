"""Cancelar, mover y resolver cambios de horario con una conexión simulada: cada rama de la regla."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from turnos_dominio import cambios, limites
from turnos_dominio.cambios import (
    CambioNoPendiente,
    CambioVencido,
    CambioYaPendiente,
    HorarioOcupado,
    HorarioPasado,
    HorarioSinCambios,
    OperacionParaReintentar,
    PlazoVencido,
    TurnoNoEncontrado,
    TurnoNoModificable,
    aprobar_cambio_horario,
    cancelar_turno_cliente,
    mover_turno_cliente,
    rechazar_cambio_horario,
)

UTC = timezone.utc  # noqa: UP017
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
TENANT = uuid.uuid4()
CLIENTE = uuid.uuid4()
RESERVA = uuid.uuid4()
PROFESIONAL = uuid.uuid4()
MEDIA_HORA = timedelta(minutes=30)


class ErrorPg(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("error de base")
        self.sqlstate = sqlstate


def _turno(**cambios_):
    base = {
        "id": RESERVA,
        "status": "confirmed",
        "end_customer_id": CLIENTE,
        "professional_id": PROFESIONAL,
        "inicio": AHORA + timedelta(days=3),
        "fin": AHORA + timedelta(days=3) + MEDIA_HORA,
        "pending_expires_at": None,
        "starts_at_propuesto": None,
        "ends_at_propuesto": None,
        "cambio_pendiente_expira_at": None,
        "modo": "auto",
    }
    base.update(cambios_)
    return base


class ConnFalsa:
    """Responde por el texto de la sentencia; guarda lo que se ejecutó y los argumentos de cada una."""

    def __init__(
        self,
        turno=None,
        plazo="24",
        horas="24",
        errores=None,
        mover="movido",
        anticipacion=None,
        liberados=(),
        una_vez=None,
    ):
        self.turno, self.plazo, self.horas, self.anticipacion = turno, plazo, horas, anticipacion
        self.liberados = list(liberados)
        self.una_vez = dict(una_vez or {})  # fragmento de SQL -> excepción que se levanta solo la primera vez
        self.errores = errores or {}  # fragmento de SQL -> excepción a levantar
        self.mover = mover
        self.hechas: list[tuple[str, tuple]] = []

    def _fallar(self, sql):
        for fragmento in list(self.una_vez):
            if fragmento in sql:
                raise self.una_vez.pop(fragmento)
        for fragmento, error in self.errores.items():
            if fragmento in sql:
                raise error

    async def execute(self, sql, *args):
        self.hechas.append((sql, args))
        self._fallar(sql)

    async def fetchval(self, sql, *args):
        self.hechas.append((sql, args))
        if "write_audit_log" in sql:
            return uuid.uuid4()
        if "cancelacion_plazo_horas" in sql:
            return self.plazo
        if "booking_min_notice_minutes" in sql:
            return self.anticipacion
        if "booking_approval_timeout_hours" in sql:
            return self.horas
        return None

    async def fetch(self, sql, *args):
        self.hechas.append((sql, args))
        return self.liberados

    async def fetchrow(self, sql, *args):
        self.hechas.append((sql, args))
        self._fallar(sql)
        if sql.lstrip().startswith("select b.id"):
            tenant, _, cliente = args
            if (
                self.turno is None
                or tenant != TENANT
                or (cliente is not None and cliente != self.turno["end_customer_id"])
            ):
                return None
            return self.turno
        if "set time_range = tstzrange($3" in sql:
            return {"id": RESERVA} if self.mover == "movido" else None
        return {"id": RESERVA}

    def sentencias(self, fragmento):
        return [(s, a) for s, a in self.hechas if fragmento in s]

    def auditorias(self):
        return [a for s, a in self.hechas if "write_audit_log" in s]


def _cancelar(conn, **extra):
    datos = {"tenant_id": TENANT, "end_customer_id": CLIENTE, "booking_id": RESERVA, "canal": "web", "ahora": AHORA}
    return cancelar_turno_cliente(conn, **{**datos, **extra})


def _mover(conn, nuevo_inicio, **extra):
    datos = {
        "tenant_id": TENANT,
        "end_customer_id": CLIENTE,
        "booking_id": RESERVA,
        "nuevo_inicio": nuevo_inicio,
        "canal": "bot",
        "ahora": AHORA,
    }
    return mover_turno_cliente(conn, **{**datos, **extra})


# --- plazo ---------------------------------------------------------------------------------------------------------


def test_exigir_plazo_en_el_borde_exacto_pasa_y_un_minuto_antes_no():
    cambios.exigir_plazo(AHORA + timedelta(hours=24), AHORA, 24)
    with pytest.raises(PlazoVencido) as error:
        cambios.exigir_plazo(AHORA + timedelta(hours=24) - timedelta(minutes=1), AHORA, 24)
    assert error.value.horas_plazo == 24
    cambios.exigir_plazo(AHORA + timedelta(hours=24, minutes=1), AHORA, 24)


def test_plazo_cero_permite_hasta_el_inicio_y_rechaza_un_turno_ya_empezado():
    cambios.exigir_plazo(AHORA, AHORA, 0)
    with pytest.raises(PlazoVencido) as error:
        cambios.exigir_plazo(AHORA - timedelta(minutes=1), AHORA, 0)
    assert error.value.horas_plazo == 0


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, 24), ("48", 48), ("0", 0), ("720", 720), ("721", 24), ("-1", 24), ("abc", 24), ("", 24), (24, 24)],
)
async def test_el_plazo_sale_del_setting_y_si_falta_o_es_basura_es_24(valor, esperado):
    assert await cambios.plazo_cancelacion_horas(ConnFalsa(plazo=valor), TENANT) == esperado


@pytest.mark.parametrize(("valor", "esperado"), [(None, 24), ("2", 2), ("168", 168), ("0", 24), ("169", 24), ("x", 24)])
async def test_las_horas_de_aprobacion_del_cambio_tienen_rango_y_default(valor, esperado):
    assert await cambios.horas_aprobacion_cambio(ConnFalsa(horas=valor), TENANT) == esperado


# --- cancelar ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("estado", ["confirmed", "pending"])
async def test_cancelar_dentro_del_plazo_cancela_y_audita(estado):
    vence = AHORA + timedelta(hours=1) if estado == "pending" else None
    conn = ConnFalsa(_turno(status=estado, pending_expires_at=vence))
    cancelado = await _cancelar(conn)
    assert cancelado.id == RESERVA and cancelado.estado_previo == estado
    assert len(conn.sentencias("set status = 'cancelled'")) == 1
    (auditoria,) = conn.auditorias()
    assert auditoria[0] == "booking_cancelled_by_customer" and auditoria[1] == "bookings"
    assert '"canal": "web"' in auditoria[3]
    assert (
        conn.hechas[0][0] == "savepoint cancelar_cliente" and conn.hechas[-1][0] == "release savepoint cancelar_cliente"
    )


async def test_cancelar_justo_en_el_plazo_pasa_y_un_minuto_antes_es_plazo_vencido():
    inicio = AHORA + timedelta(hours=24)
    await _cancelar(ConnFalsa(_turno(inicio=inicio, fin=inicio + MEDIA_HORA)))
    antes = inicio - timedelta(minutes=1)
    conn = ConnFalsa(_turno(inicio=antes, fin=antes + MEDIA_HORA))
    with pytest.raises(PlazoVencido) as error:
        await _cancelar(conn)
    assert error.value.horas_plazo == 24
    assert not conn.sentencias("set status") and not conn.auditorias()
    assert conn.hechas[-1][0] == "rollback to savepoint cancelar_cliente"


async def test_cancelar_con_plazo_cero_hasta_el_inicio_y_no_despues():
    await _cancelar(ConnFalsa(_turno(inicio=AHORA, fin=AHORA + MEDIA_HORA), plazo="0"))
    pasado = AHORA - timedelta(minutes=1)
    with pytest.raises(PlazoVencido) as error:
        await _cancelar(ConnFalsa(_turno(inicio=pasado, fin=pasado + MEDIA_HORA), plazo="0"))
    assert error.value.horas_plazo == 0


async def test_cancelar_con_el_plazo_del_negocio_y_con_el_default_si_no_lo_configuro():
    inicio = AHORA + timedelta(hours=40)
    turno = _turno(inicio=inicio, fin=inicio + MEDIA_HORA)
    await _cancelar(ConnFalsa(turno, plazo="36"))
    with pytest.raises(PlazoVencido):
        await _cancelar(ConnFalsa(turno, plazo="48"))
    await _cancelar(ConnFalsa(turno, plazo=None))  # 24 por defecto


async def test_un_pending_vencido_no_se_cancela_como_turno_vivo():
    conn = ConnFalsa(_turno(status="pending", pending_expires_at=AHORA))
    with pytest.raises(TurnoNoModificable):
        await _cancelar(conn)


@pytest.mark.parametrize("estado", ["cancelled", "completed"])
async def test_cancelar_un_turno_ya_cerrado_no_es_modificable(estado):
    with pytest.raises(TurnoNoModificable):
        await _cancelar(ConnFalsa(_turno(status=estado)))


async def test_cancelar_un_turno_ajeno_o_inexistente_es_no_encontrado_y_no_escribe():
    ajeno = ConnFalsa(_turno(end_customer_id=uuid.uuid4()))
    with pytest.raises(TurnoNoEncontrado):
        await _cancelar(ajeno)
    otro_tenant = ConnFalsa(_turno())
    with pytest.raises(TurnoNoEncontrado):
        await _cancelar(otro_tenant, tenant_id=uuid.uuid4())
    with pytest.raises(TurnoNoEncontrado):
        await _cancelar(ConnFalsa(None))
    for conn in (ajeno, otro_tenant):
        assert not conn.sentencias("set status") and not conn.auditorias()


async def test_cancelar_toma_el_lock_del_profesional_antes_de_leer_bajo_lock():
    conn = ConnFalsa(_turno())
    await _cancelar(conn)
    textos = [s for s, _ in conn.hechas]
    lock = next(i for i, s in enumerate(textos) if "turnos_profesional" in s)
    lectura = next(i for i, s in enumerate(textos) if s.endswith("for update of b"))
    assert lock < lectura


# --- mover ---------------------------------------------------------------------------------------------------------


async def test_mover_en_modo_auto_mueve_directo_con_la_misma_duracion_y_audita():
    conn = ConnFalsa(_turno(modo="auto"))
    nuevo = AHORA + timedelta(days=4)
    movido = await _mover(conn, nuevo)
    assert movido.resultado == "movido" and movido.inicio == nuevo and movido.fin == nuevo + MEDIA_HORA
    (_, args) = conn.sentencias("set time_range = tstzrange($3")[0]
    assert args == (TENANT, RESERVA, nuevo, nuevo + MEDIA_HORA)
    assert not conn.sentencias("starts_at_propuesto = $3")
    assert conn.auditorias()[0][0] == "booking_moved_by_customer"
    assert any("turnos_profesional" in s for s, _ in conn.hechas)


async def test_mover_en_modo_approval_deja_el_original_y_guarda_la_propuesta():
    conn = ConnFalsa(_turno(modo="approval"), horas="12")
    nuevo = AHORA + timedelta(days=4)
    movido = await _mover(conn, nuevo)
    assert movido.resultado == "pendiente_de_aprobacion"
    assert (movido.inicio, movido.fin) == (AHORA + timedelta(days=3), AHORA + timedelta(days=3) + MEDIA_HORA)
    assert (movido.inicio_propuesto, movido.fin_propuesto) == (nuevo, nuevo + MEDIA_HORA)
    assert movido.cambio_pendiente_expira_at == AHORA + timedelta(hours=12)
    assert not conn.sentencias("set time_range = tstzrange($3")
    assert conn.auditorias()[0][0] == "booking_move_requested"


@pytest.mark.parametrize("modo", ["deposit", "approval"])
async def test_mover_con_deposito_o_sin_asignacion_pide_aprobacion(modo):
    movido = await _mover(ConnFalsa(_turno(modo=modo)), AHORA + timedelta(days=4))
    assert movido.resultado == "pendiente_de_aprobacion"


async def test_la_propuesta_no_vence_despues_del_inicio_original():
    inicio = AHORA + timedelta(hours=30)
    conn = ConnFalsa(_turno(modo="approval", inicio=inicio, fin=inicio + MEDIA_HORA), horas="168")
    movido = await _mover(conn, AHORA + timedelta(days=4))
    assert movido.cambio_pendiente_expira_at == inicio


async def test_mover_un_pending_es_directo_aunque_el_servicio_sea_approval_y_acorta_el_vencimiento():
    conn = ConnFalsa(_turno(status="pending", modo="approval", pending_expires_at=AHORA + timedelta(hours=2)))
    nuevo = AHORA + timedelta(days=4)
    movido = await _mover(conn, nuevo)
    assert movido.resultado == "movido"
    assert conn.sentencias("least(pending_expires_at")[0][1] == (TENANT, RESERVA, nuevo)
    assert not conn.sentencias("starts_at_propuesto = $3")


async def test_mover_justo_en_el_plazo_pasa_y_un_minuto_antes_es_plazo_vencido():
    inicio = AHORA + timedelta(hours=24)
    await _mover(ConnFalsa(_turno(inicio=inicio, fin=inicio + MEDIA_HORA)), AHORA + timedelta(days=5))
    antes = inicio - timedelta(minutes=1)
    conn = ConnFalsa(_turno(inicio=antes, fin=antes + MEDIA_HORA))
    with pytest.raises(PlazoVencido):
        await _mover(conn, AHORA + timedelta(days=5))
    assert not conn.sentencias("set time_range") and not conn.sentencias("starts_at_propuesto = $3")
    assert not conn.auditorias()


async def test_mover_con_plazo_cero_hasta_el_inicio():
    await _mover(ConnFalsa(_turno(inicio=AHORA, fin=AHORA + MEDIA_HORA), plazo="0"), AHORA + timedelta(days=1))
    pasado = AHORA - timedelta(minutes=1)
    with pytest.raises(PlazoVencido):
        await _mover(ConnFalsa(_turno(inicio=pasado, fin=pasado + MEDIA_HORA), plazo="0"), AHORA + timedelta(days=1))


async def test_mover_a_un_horario_ocupado_es_horario_ocupado_en_los_dos_caminos():
    nuevo = AHORA + timedelta(days=4)
    directo = ConnFalsa(_turno(modo="auto"), mover="choque")
    directo.errores["set time_range = tstzrange($3"] = ErrorPg("23P01")
    with pytest.raises(HorarioOcupado):
        await _mover(directo, nuevo)
    propuesta = ConnFalsa(_turno(modo="approval"))
    propuesta.errores["starts_at_propuesto = $3"] = ErrorPg("23P01")  # choque con otro turno o con otra propuesta
    with pytest.raises(HorarioOcupado):
        await _mover(propuesta, nuevo)
    assert propuesta.hechas[-1][0] == "rollback to savepoint mover_cliente"
    assert not propuesta.auditorias() and not directo.auditorias()


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_un_deadlock_se_reintenta_una_vez_la_operacion_entera(sqlstate):
    conn = ConnFalsa(_turno(modo="approval"), una_vez={"starts_at_propuesto = $3": ErrorPg(sqlstate)})
    movido = await _mover(conn, AHORA + timedelta(days=4))
    assert movido.resultado == "pendiente_de_aprobacion"
    assert len(conn.sentencias("savepoint mover_cliente")) == 4  # savepoint, rollback, savepoint, release
    assert len(conn.auditorias()) == 1


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_un_deadlock_persistente_es_operacion_para_reintentar_y_nunca_horario_ocupado(sqlstate):
    for operacion in ("mover", "cancelar", "rechazar", "aprobar"):
        conn = ConnFalsa(_con_propuesta(AHORA + timedelta(hours=1)), errores={"for update of b": ErrorPg(sqlstate)})
        with pytest.raises(OperacionParaReintentar):
            if operacion == "mover":
                await _mover(conn, AHORA + timedelta(days=4))
            elif operacion == "cancelar":
                await _cancelar(conn)
            elif operacion == "rechazar":
                await rechazar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA)
            else:
                await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
        assert len(conn.sentencias("for update of b")) == 2  # se intentó dos veces, no más
        assert not conn.auditorias()


async def test_el_lock_del_profesional_se_toma_antes_de_la_fila_tambien_al_reintentar():
    conn = ConnFalsa(_turno(modo="approval"), una_vez={"starts_at_propuesto = $3": ErrorPg("40P01")})
    await _mover(conn, AHORA + timedelta(days=4))
    textos = [t for t, _ in conn.hechas]
    filas = [i for i, t in enumerate(textos) if t.endswith("for update of b")]
    assert len(filas) == 2
    for i in filas:
        assert any("turnos_profesional" in x for x in textos[:i])


async def test_un_choque_23p01_al_cancelar_no_se_disfraza_de_horario_ocupado():
    conn = ConnFalsa(_turno(), errores={"set status = 'cancelled'": ErrorPg("23P01")})
    with pytest.raises(ErrorPg):
        await _cancelar(conn)


async def test_otro_error_de_base_se_propaga_tras_volver_al_savepoint():
    conn = ConnFalsa(_turno(modo="approval"), errores={"starts_at_propuesto = $3": ErrorPg("XX000")})
    with pytest.raises(ErrorPg):
        await _mover(conn, AHORA + timedelta(days=4))
    assert conn.hechas[-1][0] == "rollback to savepoint mover_cliente"


async def test_mover_siempre_exige_la_anticipacion_del_negocio_leida_de_settings():
    conn = ConnFalsa(_turno(modo="auto"), anticipacion="60")
    with pytest.raises(limites.ReservaRechazada) as error:
        await _mover(conn, AHORA + timedelta(minutes=59))
    assert error.value.motivo == limites.MOTIVO_ANTICIPACION
    assert not conn.sentencias("set time_range")
    await _mover(ConnFalsa(_turno(modo="auto"), anticipacion="60"), AHORA + timedelta(minutes=60))
    # sin la clave rige el default de 30 minutos
    with pytest.raises(limites.ReservaRechazada):
        await _mover(ConnFalsa(_turno(modo="auto")), AHORA + timedelta(minutes=29))
    await _mover(ConnFalsa(_turno(modo="auto")), AHORA + timedelta(minutes=30))


@pytest.mark.parametrize("modo", ["auto", "approval"])
@pytest.mark.parametrize("delta", [timedelta(0), timedelta(minutes=-1), timedelta(days=-1)])
async def test_nunca_se_mueve_al_pasado_ni_a_ahora_mismo_aunque_no_haya_anticipacion(modo, delta):
    conn = ConnFalsa(_turno(modo=modo), anticipacion="0")
    with pytest.raises(HorarioPasado):
        await _mover(conn, AHORA + delta)
    assert not conn.sentencias("set time_range") and not conn.sentencias("starts_at_propuesto = $3")
    assert not conn.auditorias()


async def test_un_cliente_en_el_tope_de_turnos_puede_mover_porque_mover_no_crea_turnos():
    conn = ConnFalsa(_turno(modo="auto"))
    await _mover(conn, AHORA + timedelta(days=4))
    assert not conn.sentencias("count(*)")  # no se consulta ni el tope de activos ni el de por hora
    assert not conn.sentencias("booking_max_active_per_customer")
    assert not conn.sentencias("booking_max_per_hour")


async def test_la_propuesta_anterior_al_original_vence_cuando_empieza_su_horario():
    nuevo = AHORA + timedelta(hours=2)  # el original es en 3 días y la aprobación dura 24 horas
    movido = await _mover(ConnFalsa(_turno(modo="approval"), horas="24", anticipacion="0"), nuevo)
    assert movido.cambio_pendiente_expira_at == nuevo


async def test_aprobar_una_propuesta_cuyo_inicio_ya_paso_es_cambio_vencido():
    turno = _con_propuesta(AHORA + timedelta(hours=5))
    turno["starts_at_propuesto"] = AHORA - timedelta(minutes=1)
    conn = ConnFalsa(turno)
    with pytest.raises(CambioVencido):
        await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
    assert not conn.sentencias("set time_range") and not conn.auditorias()


async def test_aprobar_cuando_el_horario_original_ya_empezo_es_cambio_vencido_aunque_la_propuesta_siga_vigente():
    turno = _con_propuesta(AHORA + timedelta(hours=5))
    turno["inicio"], turno["fin"] = AHORA - timedelta(minutes=1), AHORA + MEDIA_HORA
    conn = ConnFalsa(turno)
    with pytest.raises(CambioVencido):
        await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
    assert not conn.sentencias("set time_range") and not conn.auditorias()


async def test_un_pending_movido_nunca_queda_con_el_vencimiento_en_el_pasado():
    conn = ConnFalsa(_turno(status="pending", modo="approval", pending_expires_at=AHORA + timedelta(hours=2)))
    nuevo = AHORA + timedelta(hours=1)
    await _mover(conn, nuevo)
    (_, args) = conn.sentencias("least(pending_expires_at")[0]
    assert args[2] == nuevo and args[2] > AHORA


def _liberado_fila():
    return {
        "tenant_id": TENANT,
        "booking_id": uuid.uuid4(),
        "channel": None,
        "channel_user_id": None,
        "notify": False,
        "end_customer_id": None,
        "starts_at": None,
        "service_name": None,
    }


@pytest.mark.parametrize("modo", ["auto", "approval"])
async def test_mover_libera_los_pending_vencidos_solapados_antes_de_escribir_en_los_dos_caminos(modo):
    fila_liberada = _liberado_fila()
    conn = ConnFalsa(_turno(modo=modo), liberados=[fila_liberada])
    nuevo = AHORA + timedelta(days=4)
    await _mover(conn, nuevo)
    # el liberado no avisa (notify falso) y aun así deja su fila de auditoría, con el canal
    liberadas = [a for a in conn.auditorias() if a[0] == "booking_pending_released_on_move"]
    assert len(liberadas) == 1
    assert liberadas[0][1] == "bookings" and liberadas[0][2] == fila_liberada["booking_id"]
    assert '"canal": "bot"' in liberadas[0][3]
    textos = [t for t, _ in conn.hechas]
    liberar = next(i for i, t in enumerate(textos) if "release_expired_pending_overlaps" in t)
    escribir = next(
        i for i, t in enumerate(textos) if "set time_range = tstzrange($3" in t or "starts_at_propuesto = $3" in t
    )
    assert liberar < escribir
    assert conn.sentencias("release_expired_pending_overlaps")[0][1] == (PROFESIONAL, nuevo, nuevo + MEDIA_HORA)


async def test_descartar_una_propuesta_vencida_deja_su_auditoria_con_el_horario_descartado():
    descartada = AHORA + timedelta(days=5)
    vencido = _turno(
        modo="approval",
        starts_at_propuesto=descartada,
        ends_at_propuesto=descartada + MEDIA_HORA,
        cambio_pendiente_expira_at=AHORA,
    )
    conn = ConnFalsa(vencido)
    await _mover(conn, AHORA + timedelta(days=4))
    acciones = [a[0] for a in conn.auditorias()]
    assert acciones == ["booking_change_expired_discarded", "booking_move_requested"]
    assert descartada.isoformat() in conn.auditorias()[0][3]


async def test_mover_al_mismo_horario_es_un_error_propio():
    with pytest.raises(HorarioSinCambios):
        await _mover(ConnFalsa(_turno()), AHORA + timedelta(days=3))


async def test_mover_un_turno_con_cambio_pendiente_vigente_es_cambio_ya_pendiente():
    pendiente = _turno(
        modo="approval",
        starts_at_propuesto=AHORA + timedelta(days=5),
        ends_at_propuesto=AHORA + timedelta(days=5) + MEDIA_HORA,
        cambio_pendiente_expira_at=AHORA + timedelta(minutes=1),
    )
    with pytest.raises(CambioYaPendiente):
        await _mover(ConnFalsa(pendiente), AHORA + timedelta(days=4))


async def test_un_cambio_pendiente_vencido_sin_revertir_se_descarta_y_se_vuelve_a_proponer():
    vencido = _turno(
        modo="approval",
        starts_at_propuesto=AHORA + timedelta(days=5),
        ends_at_propuesto=AHORA + timedelta(days=5) + MEDIA_HORA,
        cambio_pendiente_expira_at=AHORA,
    )
    conn = ConnFalsa(vencido)
    movido = await _mover(conn, AHORA + timedelta(days=4))
    assert movido.resultado == "pendiente_de_aprobacion"
    textos = [s for s, _ in conn.hechas]
    descartar = next(i for i, s in enumerate(textos) if "starts_at_propuesto = null" in s)
    proponer = next(i for i, s in enumerate(textos) if "starts_at_propuesto = $3" in s)
    assert descartar < proponer


@pytest.mark.parametrize("estado", ["cancelled", "completed"])
async def test_mover_un_turno_cerrado_no_es_modificable(estado):
    with pytest.raises(TurnoNoModificable):
        await _mover(ConnFalsa(_turno(status=estado)), AHORA + timedelta(days=4))


async def test_mover_un_turno_ajeno_es_no_encontrado_y_no_escribe():
    conn = ConnFalsa(_turno(end_customer_id=uuid.uuid4()))
    with pytest.raises(TurnoNoEncontrado):
        await _mover(conn, AHORA + timedelta(days=4))
    assert not conn.sentencias("set time_range") and not conn.sentencias("starts_at_propuesto = $3")
    assert not conn.auditorias()


# --- aprobar y rechazar --------------------------------------------------------------------------------------------


def _con_propuesta(expira):
    return _turno(
        modo="approval",
        starts_at_propuesto=AHORA + timedelta(days=5),
        ends_at_propuesto=AHORA + timedelta(days=5) + MEDIA_HORA,
        cambio_pendiente_expira_at=expira,
    )


async def test_aprobar_aplica_el_horario_propuesto_y_audita_como_staff():
    conn = ConnFalsa(_con_propuesta(AHORA + timedelta(hours=1)))
    resuelto = await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
    assert resuelto.aprobado and resuelto.inicio == AHORA + timedelta(days=5)
    assert len(conn.sentencias("set time_range = tstzrange(starts_at_propuesto")) == 1
    (auditoria,) = conn.auditorias()
    assert auditoria[0] == "booking_change_approved" and '"canal": "staff"' in auditoria[3]


async def test_aprobar_un_cambio_vencido_no_se_puede_y_no_escribe():
    conn = ConnFalsa(_con_propuesta(AHORA))
    with pytest.raises(CambioVencido):
        await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
    assert not conn.sentencias("set time_range") and not conn.auditorias()


async def test_si_la_base_dice_que_venció_en_el_instante_es_cambio_vencido():
    conn = ConnFalsa(
        _con_propuesta(AHORA + timedelta(seconds=1)),
        errores={"set time_range = tstzrange(starts_at_propuesto": ErrorPg("23514")},
    )
    with pytest.raises(CambioVencido):
        await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)


async def test_aprobar_con_choque_es_horario_ocupado():
    conn = ConnFalsa(
        _con_propuesta(AHORA + timedelta(hours=1)),
        errores={"set time_range = tstzrange(starts_at_propuesto": ErrorPg("23P01")},
    )
    with pytest.raises(HorarioOcupado):
        await aprobar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)


async def test_aprobar_sin_cambio_pendiente_o_de_otro_tenant():
    with pytest.raises(CambioNoPendiente):
        await aprobar_cambio_horario(ConnFalsa(_turno()), tenant_id=TENANT, booking_id=RESERVA, ahora=AHORA)
    conn = ConnFalsa(_con_propuesta(AHORA + timedelta(hours=1)))
    with pytest.raises(TurnoNoEncontrado):
        await aprobar_cambio_horario(conn, tenant_id=uuid.uuid4(), booking_id=RESERVA, ahora=AHORA)
    assert not conn.sentencias("set time_range") and not conn.auditorias()


async def test_rechazar_conserva_el_original_y_tambien_sirve_si_ya_vencio():
    for expira in (AHORA + timedelta(hours=1), AHORA - timedelta(hours=1)):
        conn = ConnFalsa(_con_propuesta(expira))
        resuelto = await rechazar_cambio_horario(conn, tenant_id=TENANT, booking_id=RESERVA)
        assert not resuelto.aprobado and resuelto.inicio == AHORA + timedelta(days=3)
        assert not conn.sentencias("set time_range")
        assert conn.auditorias()[0][0] == "booking_change_rejected"


async def test_rechazar_sin_cambio_pendiente_o_de_otro_tenant():
    with pytest.raises(CambioNoPendiente):
        await rechazar_cambio_horario(ConnFalsa(_turno()), tenant_id=TENANT, booking_id=RESERVA)
    conn = ConnFalsa(_con_propuesta(AHORA + timedelta(hours=1)))
    with pytest.raises(TurnoNoEncontrado):
        await rechazar_cambio_horario(conn, tenant_id=uuid.uuid4(), booking_id=RESERVA)
    assert not conn.sentencias("starts_at_propuesto = null") and not conn.auditorias()


async def test_el_canal_queda_en_la_auditoria():
    for canal in ("bot", "web"):
        conn = ConnFalsa(_turno())
        await _cancelar(conn, canal=canal)
        assert f'"canal": "{canal}"' in conn.auditorias()[0][3]
