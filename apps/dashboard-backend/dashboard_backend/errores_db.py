"""Traducción de errores de Postgres a respuestas HTTP con mensaje claro (ARCHITECTURE.md §1.7.7, §7.1).

Los `check`, FKs compuestas y triggers de la base son la validación de verdad de duraciones, precios, ventanas
horarias, rangos y `booking_approval_timeout_hours`: este módulo no los duplica, los traduce. Un dato inválido nunca
debe terminar en 500.

  check_violation (23514)             -> 422 `valor_invalido`
  foreign_key_violation (23503)       -> 422 `referencia_invalida` al escribir; 409 `en_uso` al borrar
  unique_violation (23505)            -> 409 `duplicado`
  exclusion_violation (23P01)         -> 409 `horario_ocupado` (doble reserva del profesional, §7.1)
  not_null_violation (23502), clase 22 (datos fuera de rango/formato) -> 422 `valor_invalido`

Cualquier otro error de Postgres (permisos, tabla inexistente, caída de conexión...) no se traduce: es un fallo del
servicio, no del dato, y tiene que verse como 500 y llegar a Sentry. El texto original de Postgres no se devuelve
(nombra tablas y restricciones internas), salvo el de los triggers propios, que son mensajes ya redactados para el
usuario (check_violation sin nombre de restricción).
"""

from collections.abc import Iterator
from contextlib import contextmanager

from asyncpg.exceptions import PostgresError
from fastapi import HTTPException

_MENSAJES_CHECK = {
    "services_duration_minutes_check": "La duración del servicio debe ser mayor a 0 minutos.",
    "services_price_check": "El precio no puede ser negativo.",
    "services_deposit_amount_check": "La seña no puede ser negativa.",
    "availability_rules_check": "La hora de fin debe ser posterior a la hora de inicio.",
    "availability_rules_day_of_week_check": "El día de la semana debe estar entre 0 (domingo) y 6 (sábado).",
    "availability_blocks_time_range_check": "El bloqueo debe durar más de cero: el inicio debe ser anterior al fin.",
}
_MENSAJE_CHECK_GENERICO = "Un valor no cumple las reglas de datos del sistema."
_MENSAJES_UNICO = {
    "professionals_tenant_user_id_key": "Esa cuenta de staff ya está vinculada a otro profesional.",
}
_MENSAJE_UNICO_GENERICO = "Ya existe un registro con esos datos."
_MENSAJE_REFERENCIA = "El profesional, servicio o cuenta de staff indicado no existe en este local."
_MENSAJE_EN_USO = "El registro está en uso por otros datos (por ejemplo, turnos): desactivalo en lugar de borrarlo."
_MENSAJE_DATOS = "Un valor está fuera de rango o tiene un formato inválido."
_MENSAJE_HORARIO_OCUPADO = "Ese horario se superpone con otro turno del profesional. Elegí otro horario."
_MENSAJE_RANGO_INVERTIDO = "El inicio debe ser anterior al fin."


def _http(estado: int, codigo: str, mensaje: str) -> HTTPException:
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje})


def _traducir(error: PostgresError, en_borrado: bool) -> HTTPException | None:
    estado_sql = getattr(error, "sqlstate", None) or ""
    nombre = getattr(error, "constraint_name", None)
    if estado_sql == "23514":
        if nombre is None:
            # Trigger propio: su mensaje ya está redactado para quien usa el dashboard.
            return _http(422, "valor_invalido", str(error) or _MENSAJE_CHECK_GENERICO)
        return _http(422, "valor_invalido", _MENSAJES_CHECK.get(nombre, _MENSAJE_CHECK_GENERICO))
    if estado_sql == "23503":
        if en_borrado:
            return _http(409, "en_uso", _MENSAJE_EN_USO)
        return _http(422, "referencia_invalida", _MENSAJE_REFERENCIA)
    if estado_sql == "23505":
        return _http(409, "duplicado", _MENSAJES_UNICO.get(nombre or "", _MENSAJE_UNICO_GENERICO))
    if estado_sql == "23P01":
        return _http(409, "horario_ocupado", _MENSAJE_HORARIO_OCUPADO)
    if estado_sql == "23502":
        return _http(422, "valor_invalido", "Falta un dato obligatorio.")
    if estado_sql.startswith("22"):
        if "range lower bound" in str(error):
            return _http(422, "valor_invalido", _MENSAJE_RANGO_INVERTIDO)
        return _http(422, "valor_invalido", _MENSAJE_DATOS)
    return None


@contextmanager
def traducir_errores_db(*, en_borrado: bool = False) -> Iterator[None]:
    """Envuelve las sentencias de un endpoint: los errores de datos de Postgres salen como 4xx con mensaje."""
    try:
        yield
    except PostgresError as e:
        traducido = _traducir(e, en_borrado)
        if traducido is None:
            raise
        # La transacción del endpoint queda abortada: la excepción la hace terminar en ROLLBACK.
        raise traducido from None
