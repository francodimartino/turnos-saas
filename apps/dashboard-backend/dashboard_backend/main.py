"""Punto de entrada de producción: `uvicorn dashboard_backend.main:crear_app_desde_entorno --factory`."""

import os

from fastapi import FastAPI
from platform_auth import VerificadorJWT

from dashboard_backend.app import crear_app
from dashboard_backend.config import Config


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app."""
    config = Config.desde_entorno(os.environ)
    verificador = VerificadorJWT.desde_entorno(os.environ)
    return crear_app(config, verificador=verificador)
