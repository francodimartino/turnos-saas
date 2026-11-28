"""Encolado de los avisos al cliente y al negocio: forma de lo que llega a la base y errores con código."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from turnos_negocio import plantillas_cliente, plantillas_negocio

from turnos_dominio import (
    AvisoInvalido,
    PlantillaDeAvisoInvalida,
    encolar_aviso_cliente,
    encolar_aviso_negocio,
)
from turnos_dominio import avisos as modulo_avisos

UTC = timezone.utc  # noqa: UP017
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
TURNO = uuid.uuid4()
CLIENTE = uuid.uuid4()


class ConexionFalsa:
    """Registra el SQL y los argumentos. Devuelve un id, o `None` si el cliente está anonimizado (como la base)."""

    def __init__(self, anonimizado: bool = False) -> None:
        self.anonimizado = anonimizado
        self.llamadas: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        self.llamadas.append((sql, args))
        if "enqueue_notification" in sql and self.anonimizado:
            return None
        return uuid.uuid4()


# --- al negocio ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("plantilla", sorted(plantillas_negocio()))
async def test_aviso_al_negocio_llama_a_la_funcion_del_negocio_con_plantilla_y_turno(plantilla):
    conn = ConexionFalsa()
    outbox_id = await encolar_aviso_negocio(conn, plantilla=plantilla, booking_id=TURNO, inicio=INICIO)
    assert isinstance(outbox_id, uuid.UUID)
    ((sql, args),) = conn.llamadas
    assert "encolar_aviso_negocio" in sql
    assert args[0] == plantilla
    assert json.loads(args[1]) == {"booking_id": str(TURNO), "inicio": INICIO.isoformat()}
    assert args[2] == 0  # primer intento inmediato


async def test_el_aviso_al_negocio_no_lleva_canal_ni_destinatario_ni_cliente():
    conn = ConexionFalsa()
    await encolar_aviso_negocio(conn, plantilla="aviso_negocio_reserva", booking_id=TURNO, inicio=INICIO)
    ((sql, args),) = conn.llamadas
    assert len(args) == 3  # plantilla, params y demora: ni tenant, ni canal, ni destinatario
    assert set(json.loads(args[1])) == {"booking_id", "inicio"}
    texto = (sql + json.dumps(args, default=str)).lower()
    for ajeno in ("channel", "canal", "end_customer", "destinatario", "email", "telegram"):
        assert ajeno not in texto, ajeno


async def test_el_aviso_al_negocio_acepta_una_demora_del_primer_intento():
    conn = ConexionFalsa()
    await encolar_aviso_negocio(
        conn, plantilla="aviso_negocio_reserva", booking_id=TURNO, inicio=INICIO, primer_intento_en_segundos=60
    )
    assert conn.llamadas[0][1][2] == 60


@pytest.mark.parametrize("plantilla", ["turno_confirmado", "sincronizacion_calendar", "inventada", ""])
async def test_aviso_al_negocio_con_plantilla_que_no_es_de_negocio_se_rechaza_sin_tocar_la_base(plantilla):
    conn = ConexionFalsa()
    with pytest.raises(PlantillaDeAvisoInvalida) as error:
        await encolar_aviso_negocio(conn, plantilla=plantilla, booking_id=TURNO, inicio=INICIO)
    assert error.value.codigo == "plantilla_invalida" and conn.llamadas == []


@pytest.mark.parametrize("demora", [-1, 86401])
async def test_aviso_al_negocio_con_demora_fuera_de_rango_se_rechaza_sin_tocar_la_base(demora):
    conn = ConexionFalsa()
    with pytest.raises(AvisoInvalido) as error:
        await encolar_aviso_negocio(
            conn, plantilla="aviso_negocio_reserva", booking_id=TURNO, inicio=INICIO, primer_intento_en_segundos=demora
        )
    assert error.value.codigo == "demora_invalida" and conn.llamadas == []


async def test_aviso_al_negocio_con_inicio_sin_zona_se_rechaza_sin_tocar_la_base():
    conn = ConexionFalsa()
    with pytest.raises(AvisoInvalido) as error:
        await encolar_aviso_negocio(
            conn, plantilla="aviso_negocio_reserva", booking_id=TURNO, inicio=datetime(2026, 10, 21, 12, 30)
        )
    assert error.value.codigo == "inicio_sin_zona" and conn.llamadas == []


async def test_inicio_con_otra_zona_se_guarda_con_su_desfase():
    conn = ConexionFalsa()
    local = INICIO.astimezone(timezone(timedelta(hours=-3)))
    await encolar_aviso_negocio(conn, plantilla="aviso_negocio_reserva", booking_id=TURNO, inicio=local)
    assert json.loads(conn.llamadas[0][1][1])["inicio"] == local.isoformat()


# --- al cliente ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("plantilla", sorted(plantillas_cliente()))
async def test_aviso_al_cliente_llama_a_la_funcion_del_cliente_con_ficha_canal_plantilla_y_turno(plantilla):
    conn = ConexionFalsa()
    outbox_id = await encolar_aviso_cliente(
        conn, end_customer_id=CLIENTE, canal="telegram", plantilla=plantilla, booking_id=TURNO, inicio=INICIO
    )
    assert isinstance(outbox_id, uuid.UUID)
    ((sql, args),) = conn.llamadas
    assert "enqueue_notification" in sql
    assert args[:3] == (CLIENTE, "telegram", plantilla)
    assert json.loads(args[3]) == {"booking_id": str(TURNO), "inicio": INICIO.isoformat()}
    assert args[4] == modulo_avisos.PRIMER_INTENTO_CLIENTE_EN_SEGUNDOS == 300


@pytest.mark.parametrize("canal", ["telegram", "whatsapp", "email"])
async def test_el_canal_de_origen_se_pasa_tal_cual_a_la_base(canal):
    conn = ConexionFalsa()
    await encolar_aviso_cliente(
        conn, end_customer_id=CLIENTE, canal=canal, plantilla="turno_confirmado", booking_id=TURNO, inicio=INICIO
    )
    assert conn.llamadas[0][1][1] == canal


async def test_el_aviso_al_cliente_no_lleva_texto_ni_datos_personales_en_los_params():
    conn = ConexionFalsa()
    await encolar_aviso_cliente(
        conn, end_customer_id=CLIENTE, canal="email", plantilla="turno_cancelado", booking_id=TURNO, inicio=INICIO
    )
    assert set(json.loads(conn.llamadas[0][1][3])) == {"booking_id", "inicio"}


async def test_un_cliente_anonimizado_no_encola_y_devuelve_none():
    conn = ConexionFalsa(anonimizado=True)
    resultado = await encolar_aviso_cliente(
        conn, end_customer_id=CLIENTE, canal="telegram", plantilla="turno_confirmado", booking_id=TURNO, inicio=INICIO
    )
    assert resultado is None and len(conn.llamadas) == 1


async def test_el_aviso_al_cliente_acepta_una_demora_del_primer_intento():
    conn = ConexionFalsa()
    await encolar_aviso_cliente(
        conn,
        end_customer_id=CLIENTE,
        canal="email",
        plantilla="turno_confirmado",
        booking_id=TURNO,
        inicio=INICIO,
        primer_intento_en_segundos=0,
    )
    assert conn.llamadas[0][1][4] == 0


@pytest.mark.parametrize("plantilla", ["aviso_negocio_reserva", "inventada", ""])
async def test_aviso_al_cliente_con_plantilla_que_no_es_de_cliente_se_rechaza_sin_tocar_la_base(plantilla):
    conn = ConexionFalsa()
    with pytest.raises(PlantillaDeAvisoInvalida) as error:
        await encolar_aviso_cliente(
            conn, end_customer_id=CLIENTE, canal="telegram", plantilla=plantilla, booking_id=TURNO, inicio=INICIO
        )
    assert error.value.codigo == "plantilla_invalida" and conn.llamadas == []


@pytest.mark.parametrize("canal", ["sms", "", "Telegram"])
async def test_aviso_al_cliente_con_canal_desconocido_se_rechaza_sin_tocar_la_base(canal):
    conn = ConexionFalsa()
    with pytest.raises(AvisoInvalido) as error:
        await encolar_aviso_cliente(
            conn, end_customer_id=CLIENTE, canal=canal, plantilla="turno_confirmado", booking_id=TURNO, inicio=INICIO
        )
    assert error.value.codigo == "canal_invalido" and conn.llamadas == []


@pytest.mark.parametrize("demora", [-1, 86401])
async def test_aviso_al_cliente_con_demora_fuera_de_rango_se_rechaza_sin_tocar_la_base(demora):
    conn = ConexionFalsa()
    with pytest.raises(AvisoInvalido) as error:
        await encolar_aviso_cliente(
            conn,
            end_customer_id=CLIENTE,
            canal="telegram",
            plantilla="turno_confirmado",
            booking_id=TURNO,
            inicio=INICIO,
            primer_intento_en_segundos=demora,
        )
    assert error.value.codigo == "demora_invalida" and conn.llamadas == []


async def test_aviso_al_cliente_con_inicio_sin_zona_se_rechaza_sin_tocar_la_base():
    conn = ConexionFalsa()
    with pytest.raises(AvisoInvalido) as error:
        await encolar_aviso_cliente(
            conn,
            end_customer_id=CLIENTE,
            canal="telegram",
            plantilla="turno_confirmado",
            booking_id=TURNO,
            inicio=datetime(2026, 10, 21, 12, 30),
        )
    assert error.value.codigo == "inicio_sin_zona" and conn.llamadas == []


def test_los_errores_de_aviso_son_value_error_con_codigo_y_mensaje():
    error = PlantillaDeAvisoInvalida("x")
    assert isinstance(error, AvisoInvalido) and isinstance(error, ValueError)
    assert error.codigo == "plantilla_invalida" and "x" in str(error)
