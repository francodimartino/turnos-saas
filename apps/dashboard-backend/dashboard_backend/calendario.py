"""Servicio de calendario del dashboard-backend (ARCHITECTURE.md §3.5, §4.3, §7.1).

Piezas de larga vida, creadas una vez por proceso:

- `ConfigGoogle` se lee y valida al arrancar (`config_google_desde_entorno`): ninguna request vuelve a tocar el entorno.
- Un único `httpx.AsyncClient` por app, abierto y cerrado en el lifespan (`ProveedorCalendario.abrir/cerrar`).
- Una caché de tokens de acceso a nivel de proceso, compartida por todas las operaciones.

Lo único que se arma por operación es el `ServicioCalendario` liviano, porque lee las credenciales de cada profesional
con una conexión (`platform_secrets` + contexto de tenant). Los llamadores le pasan una `ConexionBajoDemanda`, de modo
que ninguna conexión del pool queda tomada mientras se espera a Google. Una fábrica es una función
`conn -> context manager asíncrono` que entrega ese servicio; `ProveedorCalendario` es la fábrica de producción
(`app.state.calendario`) y los routers la reciben inyectada, igual que en los tests.

Sin variables de Google cargadas, el proveedor queda en el estado explícito "Calendar no configurado": entrar a la
fábrica lanza `ErrorConfiguracionCalendar` y quien la usa responde `calendar_no_configurado` sin romper nada.
"""

import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from platform_calendar import ConfigGoogle, ErrorConfiguracionCalendar, ServicioCalendario, crear_calendario
from platform_secrets import Secreto, crear_almacen

FabricaTx = Callable[[], AbstractAsyncContextManager[Any]]


class ConexionBajoDemanda:
    """Conexión con la interfaz mínima de asyncpg que abre una transacción corta por cada consulta.

    El servicio de Calendar necesita la base solo para dos lecturas puntuales (buscar la integración del profesional y
    leer su refresh token). Si se le entregara la conexión de una transacción, esta quedaría retenida mientras espera
    a Google. Con este objeto cada consulta toma una conexión del pool, la usa y la devuelve de inmediato: durante las
    llamadas HTTP no hay ninguna conexión tomada.
    """

    def __init__(self, abrir_tx: FabricaTx) -> None:
        self._abrir_tx = abrir_tx

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetchval(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any) -> Any:
        async with self._abrir_tx() as conn:
            return await conn.execute(sql, *args)


FabricaCalendario = Callable[[Any], AbstractAsyncContextManager[ServicioCalendario]]

TIMEOUT_HTTP_SEG = 10.0


def config_google_desde_entorno() -> ConfigGoogle | None:
    """Config de Google del dashboard, validada una sola vez (al arrancar).

    Ninguna variable cargada -> `None` ("Calendar no configurado"). Alguna cargada pero no todas, o la clave del
    `state` demasiado corta -> `ErrorConfiguracionCalendar` que nombra la variable (nunca su valor): la app no arranca.
    """
    return ConfigGoogle.opcional_desde_entorno(requiere_state=True)


class ProveedorCalendario:
    """Fábrica de servicios de calendario con cliente HTTP y caché de tokens compartidos por todo el proceso."""

    def __init__(
        self,
        config: ConfigGoogle | None,
        *,
        reloj: Callable[[], float] = time.time,
        timeout_seg: float = TIMEOUT_HTTP_SEG,
    ) -> None:
        self._config = config
        self._reloj = reloj
        self._timeout = timeout_seg
        self._http: httpx.AsyncClient | None = None
        self._tokens: dict[uuid.UUID, tuple[Secreto, float]] = {}

    @property
    def configurado(self) -> bool:
        return self._config is not None

    async def abrir(self) -> None:
        """Crea el cliente HTTP único (lo llama el lifespan)."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)

    async def cerrar(self) -> None:
        """Cierra el cliente HTTP y vacía la caché de tokens (lo llama el lifespan al apagar)."""
        http, self._http = self._http, None
        self._tokens.clear()
        if http is not None:
            await http.aclose()

    @asynccontextmanager
    async def __call__(self, conn: Any) -> AsyncIterator[ServicioCalendario]:
        if self._config is None:
            raise ErrorConfiguracionCalendar("Calendar no configurado")
        if self._http is None:
            raise RuntimeError("el cliente HTTP del calendario no está abierto (falta el lifespan de la app)")
        yield crear_calendario(self._config, crear_almacen(conn), self._http, self._reloj, self._tokens)
