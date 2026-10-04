"""Utilidades de test de platform_calendar: HTTP simulado, almacén de secrets en memoria y valores generados.

Ningún token ni secret es literal: se generan en cada corrida.
"""

import json
import secrets
import uuid

import httpx
import pytest
from platform_secrets import AccesoSecretoDenegado, AlmacenSecretos, Secreto

from platform_calendar import ConfigGoogle


class AlmacenEnMemoria(AlmacenSecretos):
    def __init__(self):
        self.integraciones = {}  # integration_id -> (professional_id, config, secreto)
        self.por_profesional = {}

    async def obtener(self, integration_id):
        if integration_id not in self.integraciones:
            raise AccesoSecretoDenegado("no disponible")
        return self.integraciones[integration_id][2]

    async def buscar(self, integration_type, professional_id=None):
        return self.por_profesional.get(professional_id)

    async def guardar(self, tenant_id, integration_type, config, secret):
        raise AssertionError("platform_calendar no guarda integraciones de tenant")

    async def guardar_profesional(self, professional_id, config, secret, integration_type="google_calendar"):
        sec = secret if isinstance(secret, Secreto) else Secreto(secret)
        integ = self.por_profesional.get(professional_id) or uuid.uuid4()
        self.por_profesional[professional_id] = integ
        self.integraciones[integ] = (professional_id, config, sec)
        return integ

    async def eliminar_profesional(self, professional_id):
        integ = self.por_profesional.pop(professional_id, None)
        if integ is None:
            return False
        del self.integraciones[integ]
        return True


class GoogleSimulado:
    """Transporte httpx que simula el endpoint de tokens y la Calendar API; registra los pedidos."""

    def __init__(self):
        self.pedidos = []  # (metodo, url, authorization, cuerpo)
        self.refresh_token_valido = None
        self.refresh_emitido = "rt-" + secrets.token_hex(8)
        self.access_emitido = "at-" + secrets.token_hex(8)
        self.eventos_status = {}  # event_id -> status HTTP forzado
        self.creados = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        cuerpo = request.content.decode() if request.content else ""
        self.pedidos.append((request.method, str(request.url), request.headers.get("authorization"), cuerpo))
        url = request.url
        if url.host == "oauth2.googleapis.com":
            datos = dict(p.split("=", 1) for p in cuerpo.split("&"))
            if datos["grant_type"] == "authorization_code":
                if datos["code"] == "codigo-malo":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                resp = {"access_token": self.access_emitido, "expires_in": 3600, "refresh_token": self.refresh_emitido}
                if datos["code"] == "sin-refresh":
                    del resp["refresh_token"]
                return httpx.Response(200, json=resp)
            if self.refresh_token_valido is not None and datos["refresh_token"] != self.refresh_token_valido:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": self.access_emitido, "expires_in": 3600})
        # Calendar API: /calendar/v3/calendars/primary/events[/<id>]
        partes = url.path.split("/")
        event_id = partes[-1] if partes[-1] != "events" else None
        forzado = self.eventos_status.get(event_id)
        if forzado is not None:
            return httpx.Response(forzado, json={"error": {"message": "x"}})
        if request.method == "POST":
            self.creados += 1
            return httpx.Response(200, json={"id": f"evt{self.creados}"})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": event_id})

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def cuerpos_json(self, metodo):
        return [json.loads(p[3]) for p in self.pedidos if p[0] == metodo and p[3] and "googleapis.com/calendar" in p[1]]


@pytest.fixture
def google():
    return GoogleSimulado()


@pytest.fixture
def almacen():
    return AlmacenEnMemoria()


@pytest.fixture
def config():
    return ConfigGoogle(
        client_id="cid-" + secrets.token_hex(4),
        client_secret="cs-" + secrets.token_hex(8),
        redirect_uri="https://app.example.test/oauth/google/callback",
        state_secret=secrets.token_bytes(32),
    )
