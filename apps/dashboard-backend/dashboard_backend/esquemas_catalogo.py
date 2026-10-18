"""Cuerpos de entrada del catálogo, la disponibilidad y los settings del local.

Reglas comunes:
  - Nunca hay `tenant_id` en un cuerpo: los modelos ignoran campos desconocidos y el tenant sale de la sesión.
  - Acá solo se valida forma y tipo. Los límites de negocio que ya hace cumplir la base (duración > 0, precio >= 0,
    fin > inicio, día 0-6, rango no vacío, 1-168 horas de aprobación) los decide la base y `errores_db` los traduce a
    422: así hay una sola fuente de verdad y el `check` queda ejercitado.
  - En los PATCH solo cuentan los campos enviados; `null` explícito solo se acepta donde la columna admite NULL.
"""

import json
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from dashboard_backend.valores_json import contiene_no_finito

Nombre = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Dinero = Annotated[Decimal, Field(max_digits=14, decimal_places=2, allow_inf_nan=False)]
# Una duración de más de un día no es un servicio; el piso (> 0) lo pone la base.
Minutos = Annotated[StrictInt, Field(le=1440)]
# Rango sano del smallint/integer de la base; los límites reales (0-6) los pone el `check`.
DiaDeSemana = Annotated[StrictInt, Field(ge=-100, le=100)]
Motivo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]

MODOS_ELEGIBLES = ("auto", "approval")


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Parche(_Entrada):
    """PATCH: al menos un campo, y `null` solo en los campos de `NULEABLES`."""

    NULEABLES: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _revisar(self):
        enviados = self.model_fields_set
        if not enviados:
            raise ValueError("no se envió ningún campo para modificar")
        for campo in enviados:
            if getattr(self, campo) is None and campo not in self.NULEABLES:
                raise ValueError(f"{campo} no puede ser null")
        return self

    def cambios(self) -> dict[str, Any]:
        return {campo: getattr(self, campo) for campo in type(self).model_fields if campo in self.model_fields_set}


# --- servicios --------------------------------------------------------------------------------------------------


class ServicioIn(_Entrada):
    name: Nombre
    duration_minutes: Minutos
    price: Dinero
    deposit_amount: Dinero | None = None
    is_active: StrictBool = True


class ServicioParche(_Parche):
    NULEABLES: ClassVar[frozenset[str]] = frozenset({"deposit_amount"})

    name: Nombre | None = None
    duration_minutes: Minutos | None = None
    price: Dinero | None = None
    deposit_amount: Dinero | None = None
    is_active: StrictBool | None = None


# --- profesionales ----------------------------------------------------------------------------------------------


class ProfesionalIn(_Entrada):
    name: Nombre
    tenant_user_id: uuid.UUID | None = None
    is_active: StrictBool = True


class ProfesionalParche(_Parche):
    NULEABLES: ClassVar[frozenset[str]] = frozenset({"tenant_user_id"})

    name: Nombre | None = None
    tenant_user_id: uuid.UUID | None = None
    is_active: StrictBool | None = None


class ServicioDeProfesionalIn(_Entrada):
    """`deposit` existe en la base para no requerir una migración cuando se integre el cobro, pero en el piloto no
    se puede elegir (§7.1)."""

    confirmation_mode: Literal["auto", "approval", "deposit"]

    @field_validator("confirmation_mode")
    @classmethod
    def _sin_deposit(cls, v: str) -> str:
        if v not in MODOS_ELEGIBLES:
            raise ValueError("el modo 'deposit' todavía no está disponible: el cobro de señas no está integrado")
        return v


# --- disponibilidad ---------------------------------------------------------------------------------------------


class ReglaIn(_Entrada):
    professional_id: uuid.UUID
    day_of_week: DiaDeSemana
    start_time: time
    end_time: time


class ReglaParche(_Parche):
    day_of_week: DiaDeSemana | None = None
    start_time: time | None = None
    end_time: time | None = None


def _con_zona(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("la fecha y hora necesita zona horaria (por ejemplo -03:00)")
    return v


class BloqueoIn(_Entrada):
    """Con `professional_id` null el bloqueo es de todo el local."""

    professional_id: uuid.UUID | None = None
    start_at: datetime
    end_at: datetime
    reason: Motivo | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _zona(cls, v: datetime) -> datetime:
        return _con_zona(v)

    @model_validator(mode="after")
    def _orden(self):
        # Igual (rango vacío) lo rechaza el `check` de la base; invertido ni siquiera es un rango.
        if self.end_at < self.start_at:
            raise ValueError("el inicio del bloqueo debe ser anterior a su fin")
        return self


class BloqueoParche(_Parche):
    NULEABLES: ClassVar[frozenset[str]] = frozenset({"reason"})

    start_at: datetime | None = None
    end_at: datetime | None = None
    reason: Motivo | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _zona(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _con_zona(v)


# --- settings del local -----------------------------------------------------------------------------------------

MAX_CLAVES_SETTINGS = 50
MAX_BYTES_SETTINGS = 20_000
CLAVE_HORAS_APROBACION = "booking_approval_timeout_hours"
CLAVE_CAMPOS_EXTRA = "end_customer_extra_fields"

MAX_LARGO_TEXTO_LOCAL = 500
MAX_LARGO_HORARIOS = 2000
MAX_LINEAS_HORARIOS = 30
MAX_CAMPOS_EXTRA = 20
MAX_LARGO_CLAVE_EXTRA = 64
MAX_LARGO_ETIQUETA_EXTRA = 100

# Rangos enteros de los límites de reserva; son los mismos que hace cumplir el trigger de la base (migración
# `...001700`), que sigue siendo la última palabra. Acá solo se adelanta el rechazo con un mensaje que nombra la clave.
RANGOS_ENTEROS_SETTINGS: dict[str, tuple[int, int]] = {
    CLAVE_HORAS_APROBACION: (1, 168),
    "booking_max_active_per_customer": (1, 50),
    "booking_max_per_hour": (1, 60),
    "booking_min_notice_minutes": (0, 1440),
}


def _texto(clave: str, valor: Any, maximo: int) -> None:
    if not isinstance(valor, str) or len(valor) > maximo:
        raise ValueError(f"{clave} debe ser un texto de hasta {maximo} caracteres")


def _horarios(clave: str, valor: Any) -> None:
    if isinstance(valor, str):
        _texto(clave, valor, MAX_LARGO_HORARIOS)
        return
    if not isinstance(valor, list) or len(valor) > MAX_LINEAS_HORARIOS:
        raise ValueError(f"{clave} debe ser un texto o una lista de hasta {MAX_LINEAS_HORARIOS} textos")
    for linea in valor:
        if not isinstance(linea, str) or len(linea) > MAX_LARGO_TEXTO_LOCAL:
            raise ValueError(f"{clave} debe ser una lista de textos de hasta {MAX_LARGO_TEXTO_LOCAL} caracteres")


def _campos_extra(clave: str, valor: Any) -> None:
    if not isinstance(valor, list) or len(valor) > MAX_CAMPOS_EXTRA:
        raise ValueError(f"{clave} debe ser una lista de hasta {MAX_CAMPOS_EXTRA} campos")
    for campo in valor:
        if isinstance(campo, str):
            nombre, etiqueta = campo, None
        elif isinstance(campo, dict):
            nombre, etiqueta = campo.get("key"), campo.get("label")
        else:
            raise ValueError(f"{clave} solo admite textos u objetos con 'key'")
        if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > MAX_LARGO_CLAVE_EXTRA:
            raise ValueError(f"{clave}: cada campo necesita una clave de 1 a {MAX_LARGO_CLAVE_EXTRA} caracteres")
        if etiqueta is not None and (not isinstance(etiqueta, str) or len(etiqueta) > MAX_LARGO_ETIQUETA_EXTRA):
            raise ValueError(f"{clave}: la etiqueta debe ser un texto de hasta {MAX_LARGO_ETIQUETA_EXTRA} caracteres")


def validar_valor_de_setting(clave: str, valor: Any) -> None:
    """Valida tipo y largo de una clave conocida de `tenants.settings`; las desconocidas pasan (van por los topes).

    Levanta `ValueError` con un mensaje que nombra la clave, nunca el valor. `None` (borrar la clave) siempre es válido.
    """
    if valor is None:
        return
    if clave in RANGOS_ENTEROS_SETTINGS:
        minimo, maximo = RANGOS_ENTEROS_SETTINGS[clave]
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError(f"{clave} debe ser un número entero")
        if not minimo <= valor <= maximo:
            raise ValueError(f"{clave} debe estar entre {minimo} y {maximo}")
    elif clave in ("local_direccion", "local_contacto"):
        _texto(clave, valor, MAX_LARGO_TEXTO_LOCAL)
    elif clave == "local_horarios":
        _horarios(clave, valor)
    elif clave == CLAVE_CAMPOS_EXTRA:
        _campos_extra(clave, valor)


class SettingsParche(_Entrada):
    """Mezcla de primer nivel sobre `tenants.settings`: cada clave enviada reemplaza a la existente y `null` la borra.

    El contenido es del propio tenant (datos del local para el bot, campos accesorios de end_customers). Las claves
    conocidas se validan por tipo y largo (`validar_valor_de_setting`); las desconocidas se aceptan dentro de los
    topes de claves y de tamaño. Ningún valor puede contener floats no finitos. El tope de tamaño del resultado de la
    mezcla lo aplica la ruta.
    """

    settings: dict[str, Any]

    @field_validator("settings")
    @classmethod
    def _revisar(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            raise ValueError("no se envió ninguna clave para modificar")
        if len(v) > MAX_CLAVES_SETTINGS:
            raise ValueError(f"demasiadas claves (máximo {MAX_CLAVES_SETTINGS})")
        for clave, valor in v.items():
            if not clave.strip() or len(clave) > 64:
                raise ValueError("las claves deben tener entre 1 y 64 caracteres")
            if contiene_no_finito(valor):
                raise ValueError(f"{clave} no admite valores no finitos (Infinity, NaN)")
            validar_valor_de_setting(clave, valor)
        if len(json.dumps(v)) > MAX_BYTES_SETTINGS:
            raise ValueError("los settings son demasiado grandes")
        return v
