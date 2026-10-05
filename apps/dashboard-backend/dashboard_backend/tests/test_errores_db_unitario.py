"""Traducción de errores de Postgres (asyncpg) a respuestas HTTP: los de datos son 422/409, nunca 500."""

import asyncpg.exceptions as pgx
import pytest
from fastapi import HTTPException

from dashboard_backend.errores_db import traducir_errores_db


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
