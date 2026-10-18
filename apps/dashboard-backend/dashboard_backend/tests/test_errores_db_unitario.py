"""Traducción de errores de Postgres (asyncpg) a respuestas HTTP: los de datos son 422/409, nunca 500."""

import asyncpg.exceptions as pgx
import pytest
from fastapi import HTTPException

from dashboard_backend import errores_db
from dashboard_backend.errores_db import reintentar_transaccion, traducir_errores_db


def _error(clase, mensaje="detalle interno", **atributos):
    e = clase(mensaje)
    for nombre, valor in atributos.items():
        setattr(e, nombre, valor)
    return e


def _traducido(error, **kw) -> HTTPException:
    with pytest.raises(HTTPException) as info:
        with traducir_errores_db(**kw):
            raise error
    return info.value


def test_check_de_duracion_es_422_con_mensaje_claro():
    h = _traducido(_error(pgx.CheckViolationError, constraint_name="services_duration_minutes_check"))
    assert h.status_code == 422
    assert h.detail["codigo"] == "valor_invalido"
    assert "duración" in h.detail["mensaje"]


def test_check_de_ventana_invertida_es_422():
    h = _traducido(_error(pgx.CheckViolationError, constraint_name="availability_rules_check"))
    assert h.status_code == 422
    assert "fin" in h.detail["mensaje"] and "inicio" in h.detail["mensaje"]


def test_check_desconocido_es_422_con_mensaje_generico_sin_filtrar_el_de_postgres():
    h = _traducido(_error(pgx.CheckViolationError, 'new row for relation "x" violates', constraint_name="x_check"))
    assert h.status_code == 422
    assert "relation" not in h.detail["mensaje"]


def test_check_de_trigger_sin_constraint_conserva_su_mensaje_propio():
    msg = "settings.booking_approval_timeout_hours debe ser un entero entre 1 y 168"
    h = _traducido(_error(pgx.CheckViolationError, msg))
    assert h.status_code == 422
    assert h.detail["mensaje"] == msg


def test_foreign_key_al_escribir_es_422_y_al_borrar_es_409():
    e = _error(pgx.ForeignKeyViolationError, constraint_name="bookings_tenant_service_fkey")
    assert _traducido(e).status_code == 422
    h = _traducido(_error(pgx.ForeignKeyViolationError), en_borrado=True)
    assert h.status_code == 409
    assert h.detail["codigo"] == "en_uso"


def test_unique_es_409():
    h = _traducido(_error(pgx.UniqueViolationError, constraint_name="professionals_tenant_user_id_key"))
    assert h.status_code == 409
    assert h.detail["codigo"] == "duplicado"


def test_not_null_y_errores_de_datos_son_422():
    assert _traducido(_error(pgx.NotNullViolationError)).status_code == 422
    assert _traducido(_error(pgx.DataError)).status_code == 422
    assert _traducido(_error(pgx.NumericValueOutOfRangeError)).status_code == 422


def test_un_error_de_postgres_inesperado_no_se_traduce():
    with pytest.raises(pgx.UndefinedTableError):
        with traducir_errores_db():
            raise pgx.UndefinedTableError("x")


def test_lo_que_no_es_de_postgres_pasa_intacto():
    with pytest.raises(KeyError):
        with traducir_errores_db():
            raise KeyError("x")


def test_exclusion_de_horario_es_409_con_mensaje_claro_sin_nombrar_la_restriccion():
    h = _traducido(_error(pgx.ExclusionViolationError, constraint_name="bookings_sin_doble_booking"))
    assert h.status_code == 409
    assert h.detail["codigo"] == "horario_ocupado"
    assert "superpone" in h.detail["mensaje"]
    assert "bookings_sin_doble_booking" not in str(h.detail)


# --- Mensajes y códigos exactos de cada rama (mutation testing) ---


@pytest.mark.parametrize("restriccion", sorted(errores_db._MENSAJES_CHECK))
def test_cada_check_conocido_devuelve_su_mensaje_exacto(restriccion):
    h = _traducido(_error(pgx.CheckViolationError, "interno", constraint_name=restriccion))
    assert h.status_code == 422
    assert h.detail == {"codigo": "valor_invalido", "mensaje": errores_db._MENSAJES_CHECK[restriccion]}


def test_check_desconocido_devuelve_el_mensaje_generico_exacto():
    h = _traducido(_error(pgx.CheckViolationError, "interno", constraint_name="x_check"))
    assert h.status_code == 422
    assert h.detail == {
        "codigo": "valor_invalido",
        "mensaje": "Un valor no cumple las reglas de datos del sistema.",
    }


def test_check_de_trigger_con_mensaje_vacio_usa_el_generico():
    h = _traducido(_error(pgx.CheckViolationError, ""))
    assert h.status_code == 422
    assert h.detail["codigo"] == "valor_invalido"
    assert h.detail["mensaje"] == "Un valor no cumple las reglas de datos del sistema."


def test_check_de_trigger_conserva_el_mensaje_y_el_codigo():
    h = _traducido(_error(pgx.CheckViolationError, "mensaje del trigger"))
    assert h.detail == {"codigo": "valor_invalido", "mensaje": "mensaje del trigger"}


def test_foreign_key_al_escribir_status_codigo_y_mensaje_exactos():
    h = _traducido(_error(pgx.ForeignKeyViolationError, constraint_name="bookings_tenant_service_fkey"))
    assert h.status_code == 422
    assert h.detail == {
        "codigo": "referencia_invalida",
        "mensaje": "El profesional, servicio o cuenta de staff indicado no existe en este local.",
    }


def test_foreign_key_al_borrar_status_codigo_y_mensaje_exactos():
    h = _traducido(_error(pgx.ForeignKeyViolationError), en_borrado=True)
    assert h.status_code == 409
    assert h.detail == {
        "codigo": "en_uso",
        "mensaje": "El registro está en uso por otros datos (por ejemplo, turnos): desactivalo en lugar de borrarlo.",
    }


def test_unique_con_nombre_conocido_devuelve_su_mensaje():
    h = _traducido(_error(pgx.UniqueViolationError, constraint_name="professionals_tenant_user_id_key"))
    assert h.status_code == 409
    assert h.detail == {
        "codigo": "duplicado",
        "mensaje": "Esa cuenta de staff ya está vinculada a otro profesional.",
    }


@pytest.mark.parametrize("nombre", ["otra_unique_key", None])
def test_unique_desconocido_o_sin_nombre_devuelve_el_mensaje_generico(nombre):
    h = _traducido(_error(pgx.UniqueViolationError, constraint_name=nombre))
    assert h.status_code == 409
    assert h.detail == {"codigo": "duplicado", "mensaje": "Ya existe un registro con esos datos."}


def test_exclusion_status_codigo_y_mensaje_exactos():
    h = _traducido(_error(pgx.ExclusionViolationError, constraint_name="bookings_sin_doble_booking"))
    assert h.status_code == 409
    assert h.detail == {
        "codigo": "horario_ocupado",
        "mensaje": "Ese horario se superpone con otro turno del profesional. Elegí otro horario.",
    }


def test_not_null_status_codigo_y_mensaje_exactos():
    h = _traducido(_error(pgx.NotNullViolationError))
    assert h.status_code == 422
    assert h.detail == {"codigo": "valor_invalido", "mensaje": "Falta un dato obligatorio."}


@pytest.mark.parametrize("clase", [pgx.DataError, pgx.NumericValueOutOfRangeError, pgx.InvalidDatetimeFormatError])
def test_errores_de_datos_clase_22_status_codigo_y_mensaje_exactos(clase):
    h = _traducido(_error(clase, "valor fuera de rango"))
    assert h.status_code == 422
    assert h.detail == {
        "codigo": "valor_invalido",
        "mensaje": "Un valor está fuera de rango o tiene un formato inválido.",
    }


def test_rango_invertido_devuelve_su_mensaje_propio():
    h = _traducido(_error(pgx.DataError, "range lower bound must be less than or equal to range upper bound"))
    assert h.status_code == 422
    assert h.detail == {"codigo": "valor_invalido", "mensaje": "El inicio debe ser anterior al fin."}


def test_error_inesperado_se_propaga_la_misma_instancia():
    error = pgx.UndefinedTableError("x")
    with pytest.raises(pgx.UndefinedTableError) as info:
        with traducir_errores_db():
            raise error
    assert info.value is error


def test_la_traduccion_no_filtra_el_texto_original_de_postgres():
    h = _traducido(_error(pgx.UniqueViolationError, 'duplicate key value violates "tabla_secreta"'))
    assert "tabla_secreta" not in str(h.detail)


# --- reintentar_transaccion con espera falsa ---


class _Operacion:
    """Falla con los errores dados, en orden, y después devuelve `resultado`."""

    def __init__(self, fallos, resultado="ok"):
        self.fallos = list(fallos)
        self.resultado = resultado
        self.llamadas = 0

    async def __call__(self):
        self.llamadas += 1
        if self.fallos:
            raise self.fallos.pop(0)
        return self.resultado


class _Espera:
    def __init__(self):
        self.pausas: list[float] = []

    async def __call__(self, segundos):
        self.pausas.append(segundos)


async def test_primer_exito_no_reintenta_ni_espera():
    op, espera = _Operacion([]), _Espera()
    assert await reintentar_transaccion(op, espera=espera) == "ok"
    assert op.llamadas == 1
    assert espera.pausas == []


@pytest.mark.parametrize("clase", [pgx.DeadlockDetectedError, pgx.SerializationError])
async def test_reintenta_tras_deadlock_o_serializacion_y_devuelve_el_resultado(clase):
    op, espera = _Operacion([clase("a"), clase("b")], resultado=42), _Espera()
    assert await reintentar_transaccion(op, intentos=3, espera=espera) == 42
    assert op.llamadas == 3
    assert len(espera.pausas) == 2


async def test_agotados_los_intentos_levanta_409_sin_esperar_despues_del_ultimo():
    op = _Operacion([pgx.SerializationError("x")] * 5)
    espera = _Espera()
    with pytest.raises(HTTPException) as info:
        await reintentar_transaccion(op, intentos=3, espera=espera)
    assert op.llamadas == 3
    assert len(espera.pausas) == 2
    assert info.value.status_code == 409
    assert info.value.detail == {
        "codigo": "horario_ocupado",
        "mensaje": "Ese horario se superpone con otro turno del profesional. Elegí otro horario.",
    }


async def test_con_un_solo_intento_no_reintenta_ni_espera():
    op, espera = _Operacion([pgx.DeadlockDetectedError("x")]), _Espera()
    with pytest.raises(HTTPException) as info:
        await reintentar_transaccion(op, intentos=1, espera=espera)
    assert op.llamadas == 1
    assert espera.pausas == []
    assert info.value.status_code == 409


async def test_intentos_por_defecto_son_los_configurados():
    op = _Operacion([pgx.DeadlockDetectedError("x")] * 10)
    with pytest.raises(HTTPException):
        await reintentar_transaccion(op, espera=_Espera())
    assert op.llamadas == errores_db.INTENTOS_POR_DEFECTO == 3


async def test_la_espera_crece_con_el_intento_y_lleva_jitter_acotado():
    op, espera = _Operacion([pgx.SerializationError("x")] * 2), _Espera()
    await reintentar_transaccion(op, intentos=3, espera=espera)
    base = errores_db.ESPERA_BASE_SEGUNDOS
    assert base <= espera.pausas[0] <= 2 * base
    assert 2 * base <= espera.pausas[1] <= 4 * base


async def test_error_que_no_es_de_serializacion_no_se_reintenta():
    op, espera = _Operacion([pgx.UniqueViolationError("x")]), _Espera()
    with pytest.raises(pgx.UniqueViolationError):
        await reintentar_transaccion(op, intentos=3, espera=espera)
    assert op.llamadas == 1
    assert espera.pausas == []


async def test_una_excepcion_cualquiera_tampoco_se_reintenta():
    op = _Operacion([KeyError("x")])
    with pytest.raises(KeyError):
        await reintentar_transaccion(op, espera=_Espera())
    assert op.llamadas == 1
