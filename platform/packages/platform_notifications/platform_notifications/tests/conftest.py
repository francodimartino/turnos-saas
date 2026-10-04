"""Fixtures de platform_notifications: tokens generados en cada corrida y registrador en memoria."""

import secrets
import uuid

import httpx
import pytest

from platform_notifications import RegistradorIntentos


@pytest.fixture
def token() -> str:
    # Generado en cada corrida: no hay ninguna credencial literal en el repo.
    return f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"


class RegistradorEnMemoria(RegistradorIntentos):
    def __init__(self) -> None:
        self.filas: dict[uuid.UUID, dict] = {}
        self.llamadas: list[dict] = []

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        self.llamadas.append(
            {"status": status, "retry_count": retry_count, "last_error": last_error, "id": notification_id}
        )
        nid = notification_id or uuid.uuid4()
        self.filas[nid] = {
            "end_customer_id": end_customer_id,
            "channel": channel,
            "template": template,
            "status": status,
            "retry_count": retry_count,
            "last_error": last_error,
        }
        return nid


@pytest.fixture
def registrador() -> RegistradorEnMemoria:
    return RegistradorEnMemoria()


def cliente_con(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
