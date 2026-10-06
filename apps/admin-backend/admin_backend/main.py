"""Punto de entrada de producción: `uvicorn admin_backend.main:crear_app_desde_entorno --factory`."""

import os

from fastapi import FastAPI
from platform_admin import ClienteAuthAdminHttp
from platform_auth import VerificadorJWT

from admin_backend.app import crear_app
from admin_backend.config import Config


def crear_app_desde_entorno() -> FastAPI:
    """Lee y valida el entorno (falla al arrancar si algo falta o es inseguro) y arma la app.

    La `service_role` (`SUPABASE_SERVICE_ROLE_KEY`) la toma `ClienteAuthAdminHttp`, el único lugar donde se usa.
    """
    config = Config.desde_entorno(os.environ)
    verificador = VerificadorJWT.desde_entorno(os.environ)
    cliente_auth_admin = ClienteAuthAdminHttp.desde_entorno(os.environ)
    return crear_app(config, verificador=verificador, cliente_auth_admin=cliente_auth_admin)
