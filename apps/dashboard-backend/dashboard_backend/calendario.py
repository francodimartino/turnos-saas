"""Fábrica del servicio de calendario del dashboard-backend (ARCHITECTURE.md §3.5, §4.3, §7.1).

El servicio de `platform_calendar` se arma por operación porque lee las credenciales de cada profesional con la
conexión de la transacción en curso (`platform_secrets` + contexto de tenant). Una fábrica es una función
`conn -> context manager asíncrono` que entrega un `ServicioCalendario` y libera sus recursos al salir.

La fábrica por defecto lee las credenciales del proyecto Google de las variables de entorno; si faltan, lanza
`ErrorConfiguracionCalendar` y quien la usa registra la limpieza como no realizada sin romper nada.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from platform_calendar import ConfigGoogle, ServicioCalendario, crear_calendario
from platform_secrets import crear_almacen

FabricaCalendario = Callable[[Any], AbstractAsyncContextManager[ServicioCalendario]]

TIMEOUT_HTTP_SEG = 10.0


@asynccontextmanager
async def servicio_calendario_desde_entorno(conn: Any):
    config = ConfigGoogle.desde_entorno(requiere_state=True)  # ErrorConfiguracionCalendar si falta algo
    async with httpx.AsyncClient(timeout=TIMEOUT_HTTP_SEG) as http:
        yield crear_calendario(config, crear_almacen(conn), http)
