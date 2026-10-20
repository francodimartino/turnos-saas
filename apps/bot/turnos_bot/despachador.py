"""Punto de extensión de los flujos de conversación (identidad, reserva, consultas).

Cada flujo es un handler `async def handler(ctx) -> bool`: devuelve True si atendió el mensaje (y corta la
cadena) y False si no le corresponde. Los handlers corren DENTRO de la transacción del webhook (misma
transacción que la marca de deduplicación): pueden leer y escribir con `ctx.conn`, y si levantan una excepción
se hace rollback de todo, marca incluida. Nunca envían por su cuenta: piden respuestas con `ctx.responder(...)`
y el pipeline las envía después del commit. Lo que debe correr recién después del commit y fuera de la
transacción (llamadas HTTP a terceros, como limpiar Google Calendar tras un borrado) se pide con
`ctx.despues_del_commit(accion)`.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from platform_notifications import BASE_URL_OFICIAL, Boton, MensajeEntrante

from turnos_bot.modelo_sesion import SesionBot


@dataclass(frozen=True)
class Respuesta:
    destinatario: str = field(repr=False)  # id del chat: dato del cliente
    texto: str = field(repr=False)
    botones: tuple[Boton, ...] = ()
    # Si se conoce el end_customer, el envío queda registrado en notifications_log (§6.4).
    end_customer_id: uuid.UUID | None = None
    template: str | None = None
    # Si trae `outbox_params` (y end_customer_id y template), el pipeline encola el aviso en el outbox DENTRO de la
    # transacción del webhook (solo identificadores, sin texto ni destinatario) y deja el id en `outbox_id`.
    outbox_params: dict[str, Any] | None = None
    outbox_id: uuid.UUID | None = None


@dataclass
class Recursos:
    """Lo que el webhook le entrega a las acciones posteriores al commit (no a los handlers)."""

    pool: Any
    cliente_http: Any = None
    # `fabrica_calendario(conn, cliente_http) -> ServicioCalendario`; `None` = calendario no configurado.
    fabrica_calendario: Callable[[Any, Any], Any] | None = None
    # Base de la Bot API, validada al arrancar (la oficial salvo un simulador local).
    base_url_telegram: str = BASE_URL_OFICIAL


AccionPosterior = Callable[[Recursos], Awaitable[None]]


@dataclass
class ContextoMensaje:
    conn: Any
    tenant_id: uuid.UUID
    mensaje: MensajeEntrante
    sesion: SesionBot = field(default_factory=SesionBot)  # estado mutable; se persiste al final
    respuestas: list[Respuesta] = field(default_factory=list)
    descartar_sesion: bool = False
    posteriores: list[AccionPosterior] = field(default_factory=list)

    def responder(
        self,
        texto: str,
        botones: Sequence[Boton] = (),
        *,
        end_customer_id: uuid.UUID | None = None,
        template: str | None = None,
        outbox_params: dict[str, Any] | None = None,
    ) -> None:
        self.respuestas.append(
            Respuesta(self.mensaje.chat_id, texto, tuple(botones), end_customer_id, template, outbox_params)
        )

    def despues_del_commit(self, accion: AccionPosterior) -> None:
        """Agenda `accion(recursos)` para después del commit. Si la transacción se revierte, no corre."""
        self.posteriores.append(accion)

    def terminar_sesion(self) -> None:
        """Descarta la sesión al terminar el flujo (no queda estado para el próximo mensaje)."""
        self.sesion.clear()
        self.descartar_sesion = True


Handler = Callable[[ContextoMensaje], Awaitable[bool]]

TEXTO_NO_ENTENDIDO = "No pude entender ese mensaje. Por ahora no tengo una opción para eso."


async def responder_no_entendido(ctx: ContextoMensaje) -> None:
    ctx.responder(TEXTO_NO_ENTENDIDO)


class Despachador:
    def __init__(
        self,
        handlers: Sequence[Handler] = (),
        por_defecto: Callable[[ContextoMensaje], Awaitable[None]] = responder_no_entendido,
    ) -> None:
        self._handlers: list[Handler] = list(handlers)
        self._por_defecto = por_defecto

    def registrar(self, handler: Handler) -> None:
        """Agrega un flujo al final de la cadena (el orden de registro es el orden de prueba)."""
        self._handlers.append(handler)

    async def despachar(self, ctx: ContextoMensaje) -> None:
        for handler in self._handlers:
            if await handler(ctx):
                return
        await self._por_defecto(ctx)
