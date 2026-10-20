"""Modelo tipado de la sesión de conversación y su versión (ARCHITECTURE.md §2.6).

La sesión se guarda como JSON en `bot_sessions.state`; en memoria es un `SesionBot` con un campo por flujo. Cada
flujo tiene su modelo con un campo `version`, y la sesión completa lleva además la versión del conjunto.

Regla de versionado: al leer, una sesión cuya versión (la del conjunto o la de un flujo) no es la actual, o cuya
forma no es válida, se descarta como si no existiera y el cliente vuelve al menú. Cambiar la forma de un modelo
exige subir su versión, y subirla descarta las sesiones en curso de ese momento (duran una hora como máximo). Una
sesión guardada antes de que existieran las versiones (sin ningún campo `version`) se migra si su forma es válida.

Este módulo no importa nada del resto del bot para que lo puedan usar los flujos y la persistencia sin ciclos.
"""

import copy
import uuid
from dataclasses import dataclass
from typing import Any

VERSION_SESION = 1
VERSION_RESERVA = 1

PASO_SERVICIO = "servicio"
PASO_PROFESIONAL = "profesional"
PASO_HORARIO = "horario"
PASO_CONFIRMAR = "confirmar"
_PASOS_RESERVA = (PASO_SERVICIO, PASO_PROFESIONAL, PASO_HORARIO, PASO_CONFIRMAR)

_CLAVES_RESERVA = {"version", "paso", "servicio_id", "profesional_id", "inicio", "pagina"}
_CLAVES_SESION = {"version", "reserva", "identidad"}


class SesionInvalida(ValueError):
    """La sesión guardada no tiene la versión actual o su forma no es válida (el mensaje no lleva datos)."""


def _entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _version_actual(datos: dict, actual: int) -> bool:
    """Sin `version` (formato anterior a las versiones) cuenta como la actual; con ella, tiene que ser igual."""
    version = datos.get("version", actual)
    return _entero(version) and version == actual


def _uuid_opcional(valor: Any) -> uuid.UUID | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise SesionInvalida("id con tipo inválido")
    try:
        return uuid.UUID(valor)
    except ValueError:
        raise SesionInvalida("id con formato inválido") from None


@dataclass
class SesionReserva:
    """Reserva en curso: el paso en que está y lo elegido hasta ahora."""

    paso: str
    servicio_id: uuid.UUID | None = None
    profesional_id: uuid.UUID | None = None
    inicio: int | None = None  # epoch en segundos del horario elegido
    pagina: int = 0
    version: int = VERSION_RESERVA

    @property
    def servicio(self) -> uuid.UUID:
        """Servicio elegido; los pasos posteriores al primero lo garantizan (la lectura lo valida)."""
        if self.servicio_id is None:
            raise SesionInvalida("reserva sin servicio elegido")
        return self.servicio_id

    @property
    def profesional(self) -> uuid.UUID:
        if self.profesional_id is None:
            raise SesionInvalida("reserva sin profesional elegido")
        return self.profesional_id

    @property
    def horario(self) -> int:
        if self.inicio is None:
            raise SesionInvalida("reserva sin horario elegido")
        return self.inicio

    def a_json(self) -> dict[str, Any]:
        datos: dict[str, Any] = {"version": self.version, "paso": self.paso, "pagina": self.pagina}
        if self.servicio_id is not None:
            datos["servicio_id"] = str(self.servicio_id)
        if self.profesional_id is not None:
            datos["profesional_id"] = str(self.profesional_id)
        if self.inicio is not None:
            datos["inicio"] = self.inicio
        return datos

    @classmethod
    def desde_json(cls, datos: Any) -> "SesionReserva":
        if not isinstance(datos, dict) or not set(datos) <= _CLAVES_RESERVA:
            raise SesionInvalida("reserva con forma inválida")
        # Sin `version` = guardada antes de que existieran las versiones: misma forma que la 1.
        if not _version_actual(datos, VERSION_RESERVA):
            raise SesionInvalida("reserva con versión distinta")
        paso = datos.get("paso")
        pagina = datos.get("pagina", 0)
        inicio = datos.get("inicio")
        if paso not in _PASOS_RESERVA or not _entero(pagina) or pagina < 0:
            raise SesionInvalida("reserva con paso o página inválidos")
        if inicio is not None and not _entero(inicio):
            raise SesionInvalida("reserva con horario inválido")
        reserva = cls(
            paso,
            _uuid_opcional(datos.get("servicio_id")),
            _uuid_opcional(datos.get("profesional_id")),
            inicio,
            pagina,
        )
        # Cada paso presupone lo elegido en los anteriores.
        indice = _PASOS_RESERVA.index(paso)
        if (indice >= 1 and reserva.servicio_id is None) or (indice >= 2 and reserva.profesional_id is None):
            raise SesionInvalida("reserva incoherente con su paso")
        if paso == PASO_CONFIRMAR and reserva.inicio is None:
            raise SesionInvalida("reserva incoherente con su paso")
        return reserva


@dataclass
class SesionBot:
    """Sesión de conversación: un campo por flujo, `None` si ese flujo no tiene nada en curso."""

    reserva: SesionReserva | None = None
    # Hasta que el flujo de identidad tenga su modelo propio se guarda como viene (ids y pasos como texto).
    identidad: dict[str, str] | None = None
    version: int = VERSION_SESION

    def __bool__(self) -> bool:
        return self.reserva is not None or self.identidad is not None

    def clear(self) -> None:
        self.reserva = None
        self.identidad = None

    def copia(self) -> "SesionBot":
        return copy.deepcopy(self)

    def a_json(self) -> dict[str, Any]:
        datos: dict[str, Any] = {"version": self.version}
        if self.reserva is not None:
            datos["reserva"] = self.reserva.a_json()
        if self.identidad is not None:
            datos["identidad"] = dict(self.identidad)
        return datos

    @classmethod
    def desde_json(cls, datos: Any) -> "SesionBot":
        """Reconstruye la sesión o levanta `SesionInvalida` (versión distinta o forma inválida)."""
        if not isinstance(datos, dict) or not set(datos) <= _CLAVES_SESION:
            raise SesionInvalida("sesión con forma inválida")
        if not _version_actual(datos, VERSION_SESION):
            raise SesionInvalida("sesión con versión distinta")
        reserva = datos.get("reserva")
        identidad = datos.get("identidad")
        if identidad is not None and not (
            isinstance(identidad, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in identidad.items())
        ):
            raise SesionInvalida("identidad con forma inválida")
        return cls(
            reserva=None if reserva is None else SesionReserva.desde_json(reserva),
            identidad=None if identidad is None else dict(identidad),
        )
