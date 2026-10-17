"""Limitador de intentos en memoria, de ventana deslizante, con tope de claves.

Cuenta intentos por clave (un email, una IP): con límite N, el intento N+1 dentro de la ventana se rechaza y se
informa cuántos segundos faltan para que el intento más viejo salga de la ventana. Los intentos rechazados no
cuentan. El estado vive en el proceso: con varias instancias cada una cuenta por separado (aceptable para la
escala del piloto). Nunca se loguea la clave (puede ser un email o una IP).

Configuración por variables de entorno (`limites_desde_entorno`), con estos valores por defecto:

- `LOGIN_LIMITE_IP`: intentos por IP dentro de la ventana. Por defecto 20.
- `LOGIN_LIMITE_EMAIL`: intentos por email dentro de la ventana. Por defecto 5.
- `LOGIN_VENTANA_SEG`: tamaño de la ventana en segundos. Por defecto 900 (15 minutos).

Un valor no numérico o menor que 1 se ignora y se usa el valor por defecto.
"""

import logging
import os
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LIMITE_IP_DEFECTO = 20
LIMITE_EMAIL_DEFECTO = 5
VENTANA_SEG_DEFECTO = 900
MAX_CLAVES_DEFECTO = 10_000


@dataclass(frozen=True)
class ResultadoIntento:
    permitido: bool
    espera_seg: float = 0.0


@dataclass(frozen=True)
class LimitesLogin:
    por_ip: int
    por_email: int
    ventana_seg: int


def _entero_env(nombre: str, defecto: int) -> int:
    try:
        valor = int(os.environ.get(nombre, ""))
    except ValueError:
        return defecto
    return valor if valor >= 1 else defecto


def limites_desde_entorno() -> LimitesLogin:
    return LimitesLogin(
        por_ip=_entero_env("LOGIN_LIMITE_IP", LIMITE_IP_DEFECTO),
        por_email=_entero_env("LOGIN_LIMITE_EMAIL", LIMITE_EMAIL_DEFECTO),
        ventana_seg=_entero_env("LOGIN_VENTANA_SEG", VENTANA_SEG_DEFECTO),
    )


class LimitadorIntentos:
    def __init__(
        self,
        limite: int,
        ventana_seg: float,
        max_claves: int = MAX_CLAVES_DEFECTO,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limite = limite
        self._ventana = ventana_seg
        self._max_claves = max_claves
        self._reloj = reloj
        self._intentos: OrderedDict[str, deque[float]] = OrderedDict()

    def cantidad_claves(self) -> int:
        return len(self._intentos)

    def intentar(self, clave: str) -> ResultadoIntento:
        """Registra un intento de `clave` si entra en el límite; si no, lo rechaza sin registrarlo."""
        ahora = self._reloj()
        marcas = self._intentos.get(clave)
        if marcas is not None:
            while marcas and ahora - marcas[0] >= self._ventana:
                marcas.popleft()
            if not marcas:
                del self._intentos[clave]
                marcas = None
        if marcas is not None and len(marcas) >= self._limite:
            self._intentos.move_to_end(clave)
            logger.debug("login_limite_excedido")
            return ResultadoIntento(False, self._ventana - (ahora - marcas[0]))
        if marcas is None:
            self._hacer_lugar(ahora)
            marcas = self._intentos[clave] = deque()
        marcas.append(ahora)
        self._intentos.move_to_end(clave)
        return ResultadoIntento(True)

    def _hacer_lugar(self, ahora: float) -> None:
        if len(self._intentos) < self._max_claves:
            return
        for k in [k for k, m in self._intentos.items() if not m or ahora - m[-1] >= self._ventana]:
            del self._intentos[k]
        while len(self._intentos) >= self._max_claves:
            self._intentos.popitem(last=False)
