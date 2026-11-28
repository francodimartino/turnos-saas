"""Punto de entrada de producción: `uvicorn web_backend.main:crear_app_desde_entorno --factory`."""

import os

from fastapi import FastAPI
from turnos_almacenamiento import ClienteAlmacenamiento

from web_backend.app import crear_app
from web_backend.config import Config

# Plazo total de cada llamada a Storage para firmar el logo: la página no espera más que esto por una imagen.
TIMEOUT_STORAGE_SEG = 3.0


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app."""
    config = Config.desde_entorno(os.environ)
    almacenamiento = None
    if config.storage_configurado and config.supabase_url is not None:
        almacenamiento = ClienteAlmacenamiento(
            config.supabase_url, config.supabase_service_key, timeout=TIMEOUT_STORAGE_SEG
        )
    return crear_app(config, almacenamiento=almacenamiento)
