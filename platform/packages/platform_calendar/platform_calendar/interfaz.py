"""Interfaz de calendario que ven las apps (§1.6): nada acá menciona Google ni HTTP."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NamedTuple

from platform_secrets import Secreto


class AccionSync(Enum):
    CREADO = "creado"
    ACTUALIZADO = "actualizado"
    BORRADO = "borrado"
    IGNORADO = "ignorado"
    SIN_CONEXION = "sin_conexion"  # el profesional no conectó su calendario: no es un error


@dataclass(frozen=True)
class EventoCalendario:
    """Un evento genérico: lo que la app quiere ver en el calendario, ya armado y sin ningún concepto de negocio.

    El paquete no sabe de servicios, clientes ni teléfonos: cada app decide el `titulo` y la `descripcion` (y qué
    datos personales muestra) y los entrega acá. `inicio` y `fin` llevan zona horaria. `id_externo` es el id del
    evento ya creado en el calendario (`None` si todavía no se creó); lo devuelve `sincronizar_evento` para que la
    app lo guarde.
    """

    titulo: str
    descripcion: str
    inicio: datetime  # con zona horaria
    fin: datetime  # con zona horaria
    id_externo: str | None = None


class TextoNeutro(NamedTuple):
    """Título y descripción con los que se reescribe un evento para quitarle los datos personales.

    Los define la app (el paquete no conoce el negocio): se pasan a `anonimizar_eventos`.
    """

    titulo: str
    descripcion: str


@dataclass(frozen=True)
class BookingCalendario:
    """API anterior, a retirar: lo mínimo de un booking (en código nuevo usar `EventoCalendario`)."""

    id: uuid.UUID
    status: str  # 'pending' | 'confirmed' | 'cancelled' | 'completed'
    professional_id: uuid.UUID
    servicio: str
    cliente_nombre: str
    inicio: datetime  # con zona horaria
    fin: datetime  # con zona horaria
    google_calendar_event_id: str | None = None


@dataclass(frozen=True)
class ResultadoSync:
    accion: AccionSync
    event_id: str | None  # el que el llamador debe guardar en `bookings.google_calendar_event_id` (None si se borró)


class EventoNoLimpiado(NamedTuple):
    """Un evento que no se pudo anonimizar; el llamador lo registra como `calendar_scrub_failed` (sin PII).

    `motivo`: token_vencido | conexion_eliminada | evento_no_encontrado | sin_permiso | error_api | error_red |
    error_inesperado.
    """

    integration_id: uuid.UUID
    event_id: str
    motivo: str


@dataclass(frozen=True)
class EstadoOAuth:
    """Lo que lleva el `state` firmado: a quién pertenece la conexión y el nonce de un solo uso.

    `user_id` es el usuario de la sesión que inició la conexión; `nonce` y `expira` (epoch en segundos, el `exp`
    firmado) son lo que el llamador necesita para consumir el nonce una sola vez en la base.
    """

    tenant_id: uuid.UUID
    professional_id: uuid.UUID
    user_id: uuid.UUID
    nonce: str
    expira: float


class ServicioCalendario(ABC):
    @abstractmethod
    def url_autorizacion(self, tenant_id: uuid.UUID, professional_id: uuid.UUID, user_id: uuid.UUID) -> str:
        """URL a la que se redirige al profesional para que autorice el acceso a su calendario.

        El `state` queda atado al usuario de la sesión que inicia la conexión (`user_id`).
        """

    @abstractmethod
    def verificar_state(self, state: str) -> EstadoOAuth:
        """Valida firma y vigencia del `state`; levanta `ErrorEstadoOAuth` si no es válido."""

    @abstractmethod
    async def completar_autorizacion(
        self, code: str, state: str, tenant_id_esperado: uuid.UUID, user_id_esperado: uuid.UUID
    ) -> uuid.UUID:
        """Canjea el código, guarda las credenciales del profesional y devuelve el id de la integración.

        Rechaza (`ErrorEstadoOAuth`, sin llamar a Google) un `state` de otro tenant o de otro usuario. El nonce de un
        solo uso lo consume el llamador (necesita la base) antes de invocar esto.
        """

    @abstractmethod
    async def token_de_acceso(self, integration_id: uuid.UUID) -> Secreto:
        """Token de acceso vigente de la integración (refresca si hace falta)."""

    @abstractmethod
    def descartar_token(self, integration_id: uuid.UUID) -> None:
        """Olvida el token de acceso cacheado de la integración (al desconectarla o cuando se revoca).

        Solo afecta a esa integración; si no había token cacheado, no hace nada.
        """

    @abstractmethod
    async def crear_evento(self, integration_id: uuid.UUID, booking: BookingCalendario) -> str:
        """Crea el evento y devuelve su id."""

    @abstractmethod
    async def actualizar_evento(self, integration_id: uuid.UUID, event_id: str, booking: BookingCalendario) -> None:
        """Reescribe el evento (por ejemplo, al mover el turno)."""

    @abstractmethod
    async def borrar_evento(self, integration_id: uuid.UUID, event_id: str) -> None:
        """Borra el evento; si ya no existe, no es error."""

    @abstractmethod
    async def sincronizar_evento(self, profesional_id: uuid.UUID, evento: EventoCalendario) -> ResultadoSync:
        """Deja `evento` en el calendario del profesional: lo crea si `id_externo` es `None`, si no lo reescribe.

        Devuelve `SIN_CONEXION` (sin tocar nada) si el profesional no conectó su calendario; en los demás casos
        `event_id` es el id que la app debe guardar.
        """

    @abstractmethod
    async def retirar_evento(self, profesional_id: uuid.UUID, id_externo: str | None) -> ResultadoSync:
        """Borra el evento `id_externo` del calendario del profesional (si ya no existe, no es error).

        Sin `id_externo` devuelve `IGNORADO`; sin calendario conectado, `SIN_CONEXION` conservando el id.
        """

    @abstractmethod
    async def anonimizar_eventos(
        self, pares: list[tuple[uuid.UUID, str]], texto: TextoNeutro
    ) -> list[EventoNoLimpiado]:
        """Reescribe cada evento `(integration_id, event_id)` con `texto` y sin invitados.

        No lanza: devuelve los eventos que no se pudieron limpiar, con su motivo.
        """

    @abstractmethod
    async def sincronizar_booking(self, booking: BookingCalendario) -> ResultadoSync:
        """Aplica al calendario el estado del booking (§7.1). `pending` nunca va al calendario."""

    @abstractmethod
    async def limpiar_eventos(self, pares: list[tuple[uuid.UUID, str]]) -> list[EventoNoLimpiado]:
        """Quita nombre y teléfono de cada evento `(integration_id, event_id)`. No lanza: devuelve los fallidos."""
