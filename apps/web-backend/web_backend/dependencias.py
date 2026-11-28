"""Dependencias de FastAPI de la web pública (ARCHITECTURE.md §3.1.3, §5.4.3).

Los routers de módulo resuelven en cada request lo que `crear_app` dejó en `app.state`: el pool de `web_reservas`, el
reloj y el cliente de Storage. Una prueba puede reemplazar `app.state` o cada función en `app.dependency_overrides`.

El tenant de una transacción sale siempre de resolver el slug con la base, nunca del body, el query ni los headers.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Request
from platform_tenancy import tenant_tx


def pool_actual(request: Request) -> Any:
    """El pool de `web_reservas` de esta app."""
    pool = request.app.state.pool.pool
    if pool is None:
        raise RuntimeError("el pool de la base no está inicializado")
    return pool


def reloj_actual(request: Request) -> Callable[[], datetime]:
    """Devuelve la función que da el instante actual con zona horaria."""
    return request.app.state.reloj


def almacenamiento_actual(request: Request) -> Any:
    """El cliente de Storage para firmar el logo, o `None` si el servicio no lo tiene configurado."""
    return request.app.state.almacenamiento


@asynccontextmanager
async def abrir_sin_contexto(pool: Any) -> AsyncIterator[Any]:
    """Una conexión sin contexto de tenant: solo para resolver el slug con la función pública de la base."""
    async with pool.acquire() as conn:
        yield conn


def abrir_negocio(pool: Any, tenant_id: uuid.UUID) -> Any:
    """Transacción con `app.current_tenant_id` del negocio ya resuelto: la RLS limita todo a ese negocio."""
    return tenant_tx(pool, tenant_id)
