"""Punto de entrada de producción: `uvicorn web_backend.main:crear_app_desde_entorno --factory --no-access-log`.

El servicio apaga por sí mismo el access log de uvicorn (`app.apagar_access_log`); la bandera lo deja explícito.
"""

import os

from fastapi import FastAPI

from web_backend.app import crear_app
from web_backend.config import Config


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app."""
    return crear_app(Config.desde_entorno(os.environ))
