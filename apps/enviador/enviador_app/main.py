"""Punto de entrada de producción: `uvicorn enviador_app.main:crear_app_desde_entorno --factory`."""

import os

from fastapi import FastAPI

from enviador_app.app import crear_app
from enviador_app.config import Config


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app."""
    return crear_app(Config.desde_entorno(os.environ))
