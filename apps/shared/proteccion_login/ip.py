"""IP real del cliente detrás de proxies.

Solo se confía en un header si la variable `LOGIN_IP_HEADER` lo pide (`CF-Connecting-IP` o `X-Forwarded-For`).
Sin la variable se usa `request.client.host`: un header enviado por el cliente se ignora, porque cualquiera puede
falsificarlo para esquivar el límite de intentos.

- `CF-Connecting-IP`: la pone Cloudflare; se toma tal cual.
- `X-Forwarded-For`: cada proxy agrega la IP de quien le habló al final de la lista; lo que está antes puede
  venir falsificado por el cliente. `LOGIN_PROXIES_CONFIABLES` (por defecto 1) es la cantidad de proxies propios
  delante de la app; se toma el valor que dejó el más cercano a la app de esos, contando desde la derecha.

Si el header configurado falta o no es una IP válida, se cae a `request.client.host`. La IP no se loguea.
"""

import ipaddress
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

IP_DESCONOCIDA = "desconocida"
_HEADERS_SOPORTADOS = {"cf-connecting-ip", "x-forwarded-for"}


def _es_ip(valor: str) -> bool:
    try:
        ipaddress.ip_address(valor)
    except ValueError:
        return False
    return True


def _proxies_confiables() -> int:
    try:
        return max(1, int(os.environ.get("LOGIN_PROXIES_CONFIABLES", "1")))
    except ValueError:
        return 1


def _del_header(request: Any, nombre: str) -> str | None:
    crudo = request.headers.get(nombre)
    if not crudo:
        return None
    if nombre == "x-forwarded-for":
        saltos = [s.strip() for s in crudo.split(",") if s.strip()]
        n = _proxies_confiables()
        if len(saltos) < n:
            return None
        candidato = saltos[-n]
    else:
        candidato = crudo.strip()
    return candidato if _es_ip(candidato) else None


def ip_real(request: Any) -> str:
    nombre = os.environ.get("LOGIN_IP_HEADER", "").strip().lower()
    if nombre:
        if nombre not in _HEADERS_SOPORTADOS:
            logger.warning("login_ip_header_no_soportado")
        else:
            ip = _del_header(request, nombre)
            if ip:
                return ip
            logger.debug("login_ip_header_ausente_o_invalido")
    client = getattr(request, "client", None)
    return client.host if client and client.host else IP_DESCONOCIDA
