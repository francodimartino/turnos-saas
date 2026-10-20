"""Implementación sobre Google Calendar API v3 y OAuth2, con `httpx` inyectable (sin SDK)."""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from platform_secrets import ErrorSecretos, Secreto
from platform_secrets.interfaz import AlmacenSecretos

from platform_calendar.config import ConfigGoogle
from platform_calendar.errores import (
    ErrorApiCalendar,
    ErrorCalendar,
    ErrorConexionEliminada,
    ErrorConfiguracionCalendar,
    ErrorEstadoOAuth,
    ErrorOAuth,
    ErrorRedCalendar,
    ErrorTokenVencido,
)
from platform_calendar.interfaz import (
    AccionSync,
    BookingCalendario,
    EstadoOAuth,
    EventoCalendario,
    EventoNoLimpiado,
    ResultadoSync,
    ServicioCalendario,
    TextoNeutro,
)

URL_AUTORIZACION = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública del endpoint, no un secreto
URL_EVENTOS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
ZONA_HORARIA = "America/Argentina/Buenos_Aires"
VIGENCIA_STATE_SEG = 600
MARGEN_TOKEN_SEG = 60
TIPO_INTEGRACION = "google_calendar"

_MOTIVO_POR_STATUS = {
    401: "token_vencido",
    403: "sin_permiso",
    404: "evento_no_encontrado",
    410: "evento_no_encontrado",
}


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


class CalendarioGoogle(ServicioCalendario):
    def __init__(
        self,
        config: ConfigGoogle,
        almacen: AlmacenSecretos,
        http: httpx.AsyncClient,
        reloj: Callable[[], float] = time.time,
        cache_tokens: dict[uuid.UUID, tuple[Secreto, float]] | None = None,
    ) -> None:
        self._config = config
        self._almacen = almacen
        self._http = http
        self._reloj = reloj
        # Caché de tokens de acceso por integración. Si el llamador pasa un dict, se comparte entre instancias
        # (un servicio por operación, una caché por proceso); sin dict, la caché vive lo que viva esta instancia.
        self._tokens: dict[uuid.UUID, tuple[Secreto, float]] = {} if cache_tokens is None else cache_tokens

    # --- OAuth ---------------------------------------------------------------------------------------------

    def _firmar(self, cuerpo: str) -> str:
        if self._config.state_secret is None:
            raise ErrorConfiguracionCalendar(
                "falta CALENDAR_OAUTH_STATE_SECRET: este servicio no puede iniciar ni completar OAuth"
            )
        return _b64(hmac.new(self._config.state_secret, cuerpo.encode(), hashlib.sha256).digest())

    def _state(self, tenant_id: uuid.UUID, professional_id: uuid.UUID, user_id: uuid.UUID) -> str:
        carga = {
            "t": str(tenant_id),
            "p": str(professional_id),
            "u": str(user_id),
            "exp": self._reloj() + VIGENCIA_STATE_SEG,
            "n": secrets.token_hex(16),
        }
        cuerpo = _b64(json.dumps(carga, separators=(",", ":")).encode())
        return cuerpo + "." + self._firmar(cuerpo)

    def url_autorizacion(self, tenant_id: uuid.UUID, professional_id: uuid.UUID, user_id: uuid.UUID) -> str:
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": self._state(tenant_id, professional_id, user_id),
        }
        return URL_AUTORIZACION + "?" + urlencode(params)

    def verificar_state(self, state: str) -> EstadoOAuth:
        self._firmar("")  # sin state_secret falla con ErrorConfiguracionCalendar, no como un state inválido
        try:
            cuerpo, firma = state.split(".")
            if not hmac.compare_digest(firma, self._firmar(cuerpo)):
                raise ValueError("firma")
            carga = json.loads(_desb64(cuerpo))
            if float(carga["exp"]) < self._reloj():
                raise ValueError("vencido")
            nonce = carga["n"]
            if not isinstance(nonce, str) or not nonce:
                raise ValueError("nonce")
            return EstadoOAuth(
                uuid.UUID(carga["t"]), uuid.UUID(carga["p"]), uuid.UUID(carga["u"]), nonce, float(carga["exp"])
            )
        except Exception:
            raise ErrorEstadoOAuth("state inválido o vencido") from None

    async def _pedir_token(self, datos: dict[str, str]) -> dict[str, Any]:
        enviado = {**datos, "client_id": self._config.client_id, "client_secret": self._config.client_secret}
        try:
            resp = await self._http.post(URL_TOKEN, data=enviado)
        except httpx.HTTPError:
            raise ErrorRedCalendar("no se pudo contactar a Google") from None
        if resp.status_code == 200:
            try:
                return dict(resp.json())
            except ValueError:
                raise ErrorOAuth("respuesta de Google ilegible") from None
        if datos["grant_type"] == "refresh_token" and resp.status_code in (400, 401):
            raise ErrorTokenVencido("el refresh_token ya no es válido: hay que reconectar Google")
        raise ErrorOAuth(f"Google rechazó el pedido de token ({resp.status_code})")

    async def completar_autorizacion(
        self, code: str, state: str, tenant_id_esperado: uuid.UUID, user_id_esperado: uuid.UUID
    ) -> uuid.UUID:
        estado = self.verificar_state(state)
        if estado.tenant_id != tenant_id_esperado or estado.user_id != user_id_esperado:
            raise ErrorEstadoOAuth("state inválido o vencido")
        resp = await self._pedir_token(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self._config.redirect_uri}
        )
        refresh = resp.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise ErrorOAuth("Google no entregó refresh_token (reconectar con consentimiento)")
        return await self._almacen.guardar_profesional(
            estado.professional_id,
            {"calendar_id": "primary"},
            Secreto(json.dumps({"refresh_token": refresh})),
            TIPO_INTEGRACION,
        )

    def descartar_token(self, integration_id: uuid.UUID) -> None:
        self._tokens.pop(integration_id, None)

    async def token_de_acceso(self, integration_id: uuid.UUID) -> Secreto:
        en_cache = self._tokens.get(integration_id)
        if en_cache is not None and en_cache[1] > self._reloj():
            return en_cache[0]
        try:
            secreto = await self._almacen.obtener(integration_id)
        except ErrorSecretos:
            raise ErrorConexionEliminada("la integración de calendario no existe") from None
        if secreto is None:
            raise ErrorConexionEliminada("la integración de calendario no tiene credenciales")
        try:
            refresh = json.loads(secreto.revelar())["refresh_token"]
        except Exception:
            raise ErrorConexionEliminada("credenciales de calendario ilegibles") from None
        try:
            resp = await self._pedir_token({"grant_type": "refresh_token", "refresh_token": refresh})
        except ErrorTokenVencido:
            self.descartar_token(integration_id)  # revocada (invalid_grant): no queda ningún token vivo en la caché
            raise
        token = resp.get("access_token")
        if not isinstance(token, str) or not token:
            raise ErrorOAuth("Google no entregó access_token")
        acceso = Secreto(token)
        self._tokens[integration_id] = (acceso, self._reloj() + float(resp.get("expires_in", 0)) - MARGEN_TOKEN_SEG)
        return acceso

    # --- Eventos -------------------------------------------------------------------------------------------

    async def _evento(
        self, integration_id: uuid.UUID, metodo: str, event_id: str | None = None, cuerpo: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        token = await self.token_de_acceso(integration_id)
        url = URL_EVENTOS + ("/" + quote(event_id, safe="") if event_id else "")
        try:
            resp = await self._http.request(
                metodo, url, json=cuerpo, headers={"Authorization": "Bearer " + token.revelar()}
            )
        except httpx.HTTPError:
            raise ErrorRedCalendar("no se pudo contactar a Google") from None
        if resp.status_code == 401:
            self.descartar_token(integration_id)  # el token cacheado ya no vale: el próximo pedido pide otro
        if resp.status_code >= 400:
            raise ErrorApiCalendar(resp.status_code)
        try:
            return dict(resp.json()) if resp.content else {}
        except ValueError:
            return {}

    @staticmethod
    def _cuerpo_evento(e: EventoCalendario) -> dict[str, Any]:
        if e.inicio.tzinfo is None or e.fin.tzinfo is None:
            raise ValueError("inicio y fin deben tener zona horaria")
        return {
            "summary": e.titulo,
            "description": e.descripcion,
            "start": {"dateTime": e.inicio.isoformat(), "timeZone": ZONA_HORARIA},
            "end": {"dateTime": e.fin.isoformat(), "timeZone": ZONA_HORARIA},
        }

    @staticmethod
    def _evento_de_booking(b: BookingCalendario) -> EventoCalendario:
        return EventoCalendario(
            titulo=f"{b.servicio} — {b.cliente_nombre}",
            descripcion=f"Servicio: {b.servicio}\nCliente: {b.cliente_nombre}",
            inicio=b.inicio,
            fin=b.fin,
            id_externo=b.google_calendar_event_id,
        )

    async def _crear(self, integration_id: uuid.UUID, evento: EventoCalendario) -> str:
        resp = await self._evento(integration_id, "POST", cuerpo=self._cuerpo_evento(evento))
        event_id = resp.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ErrorCalendar("Google no devolvió el id del evento")
        return event_id

    async def crear_evento(self, integration_id: uuid.UUID, booking: BookingCalendario) -> str:
        return await self._crear(integration_id, self._evento_de_booking(booking))

    async def actualizar_evento(self, integration_id: uuid.UUID, event_id: str, booking: BookingCalendario) -> None:
        await self._evento(integration_id, "PUT", event_id, self._cuerpo_evento(self._evento_de_booking(booking)))

    async def borrar_evento(self, integration_id: uuid.UUID, event_id: str) -> None:
        try:
            await self._evento(integration_id, "DELETE", event_id)
        except ErrorApiCalendar as e:
            if e.status not in (404, 410):
                raise

    async def sincronizar_evento(self, profesional_id: uuid.UUID, evento: EventoCalendario) -> ResultadoSync:
        integ = await self._almacen.buscar(TIPO_INTEGRACION, profesional_id)
        if integ is None:
            return ResultadoSync(AccionSync.SIN_CONEXION, evento.id_externo)
        if evento.id_externo:
            await self._evento(integ, "PUT", evento.id_externo, self._cuerpo_evento(evento))
            return ResultadoSync(AccionSync.ACTUALIZADO, evento.id_externo)
        return ResultadoSync(AccionSync.CREADO, await self._crear(integ, evento))

    async def retirar_evento(self, profesional_id: uuid.UUID, id_externo: str | None) -> ResultadoSync:
        if not id_externo:
            return ResultadoSync(AccionSync.IGNORADO, None)
        integ = await self._almacen.buscar(TIPO_INTEGRACION, profesional_id)
        if integ is None:
            return ResultadoSync(AccionSync.SIN_CONEXION, id_externo)
        await self.borrar_evento(integ, id_externo)
        return ResultadoSync(AccionSync.BORRADO, None)

    async def sincronizar_booking(self, booking: BookingCalendario) -> ResultadoSync:
        evento = booking.google_calendar_event_id
        estado = booking.status
        if estado in ("pending", "completed"):
            return ResultadoSync(AccionSync.IGNORADO, evento)
        if estado == "cancelled" and not evento:
            return ResultadoSync(AccionSync.IGNORADO, None)
        if estado not in ("confirmed", "cancelled"):
            raise ErrorCalendar(f"estado de booking desconocido: {estado}")
        integ = await self._almacen.buscar(TIPO_INTEGRACION, booking.professional_id)
        if integ is None:
            return ResultadoSync(AccionSync.SIN_CONEXION, evento)
        if estado == "cancelled":
            assert evento is not None  # noqa: S101 - estrechamiento de tipo para mypy; "cancelled" siempre trae evento
            await self.borrar_evento(integ, evento)
            return ResultadoSync(AccionSync.BORRADO, None)
        if evento:
            await self.actualizar_evento(integ, evento, booking)
            return ResultadoSync(AccionSync.ACTUALIZADO, evento)
        return ResultadoSync(AccionSync.CREADO, await self.crear_evento(integ, booking))

    async def limpiar_eventos(self, pares: list[tuple[uuid.UUID, str]]) -> list[EventoNoLimpiado]:
        return await self.anonimizar_eventos(
            pares, TextoNeutro("Turno (cliente anonimizado)", "Datos del cliente eliminados.")
        )

    async def anonimizar_eventos(
        self, pares: list[tuple[uuid.UUID, str]], texto: TextoNeutro
    ) -> list[EventoNoLimpiado]:
        fallidos: list[EventoNoLimpiado] = []
        anonimo = {"summary": texto.titulo, "description": texto.descripcion, "attendees": []}
        for integration_id, event_id in pares:
            try:
                await self._evento(integration_id, "PATCH", event_id, anonimo)
            except ErrorTokenVencido:
                motivo = "token_vencido"
            except ErrorConexionEliminada:
                motivo = "conexion_eliminada"
            except ErrorApiCalendar as e:
                motivo = _MOTIVO_POR_STATUS.get(e.status, "error_api")
            except ErrorRedCalendar:
                motivo = "error_red"
            except Exception:
                motivo = "error_inesperado"
            else:
                continue
            fallidos.append(EventoNoLimpiado(integration_id, event_id, motivo))
        return fallidos
