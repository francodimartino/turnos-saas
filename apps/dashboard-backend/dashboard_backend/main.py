"""Punto de entrada de producción: `uvicorn dashboard_backend.main:crear_app_desde_entorno --factory`."""

import os

from fastapi import FastAPI
from platform_auth import SupabaseAuthAdmin, VerificadorJWT

from dashboard_backend.app import crear_app
from dashboard_backend.calendario import config_google_desde_entorno
from dashboard_backend.config import Config


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app."""
    config = Config.desde_entorno(os.environ)
    verificador = VerificadorJWT.desde_entorno(os.environ)
    config_google = config_google_desde_entorno()  # falla al arrancar si quedó a medias o es insegura
    # Cliente de la Auth Admin API (invitar al staff): se crea una vez; falla al arrancar si faltan las variables.
    cliente_admin = SupabaseAuthAdmin.desde_entorno(os.environ)
    return crear_app(config, verificador=verificador, cliente_admin=cliente_admin, config_google=config_google)
