"""Parámetros de `/auth/*` del admin-backend (ARCHITECTURE.md §3.1.5, §3.6).

Login, MFA (enrolar y verificar), refrescar, logout y sesión los define una sola vez `turnos_sesion.rutas_auth`
(límites de intentos por IP y por email, Turnstile, cookies, auditoría); acá solo se arma lo propio del panel
(`servicios_auth`) y `crear_app` lo deja en `app.state.auth`:

  - Solo entra un platform admin (403 `sin_acceso_admin`, sin cookies) y todo platform admin tiene que tener MFA: no
    hay `siguiente_paso: listo` sin `aal2`.
  - La auditoría escribe en `audit_log` con el actor `platform_admin` y sin tenant. Es de mejor esfuerzo: si falla,
    queda un log de error sin datos y el flujo sigue.
  - A las acciones compartidas suma `auth_login_failed` (un login rechazado por Supabase o de una cuenta que no es
    admin): actor ausente y solo un código en la metadata, nunca email, IP, contraseña ni token.
  - `GET /auth/sesion` devuelve user_id y aal.
"""

import logging
from collections.abc import Callable
from contextlib import aclosing
from typing import Any

from platform_audit import registrar
from platform_auth import VerificadorJWT
from platform_tenancy import admin_tx
from proteccion_login import LimitadorIntentos, VerificadorTurnstile
from supabase_proxy import ClienteSupabaseAuth, ErrorSupabaseAuth
from turnos_sesion import ServiciosAuth, sesion_de_app

from admin_backend.config import Config

_log = logging.getLogger("admin_backend.auth")
NOMBRE_TOTP = "Turnos Admin"
AUDIT_LOGIN_FALLIDO = "auth_login_failed"


async def _cuerpo_sesion(request: Any) -> dict[str, Any]:
    async with aclosing(sesion_de_app(request)) as generador:
        s = await anext(generador)
    return {"user_id": str(s.user_id), "aal": s.aal}


def servicios_auth(
    config: Config,
    cliente: ClienteSupabaseAuth,
    verificador: VerificadorJWT,
    turnstile: VerificadorTurnstile | None = None,
    obtener_pool: Callable[[], Any] | None = None,
) -> ServiciosAuth:
    """Lo que el admin parametriza en las rutas compartidas; `crear_app` lo deja en `app.state.auth`."""

    async def auditar(accion: str, quien: Any, metadata: dict[str, Any] | None = None) -> None:
        """Escribe el evento en `audit_log` sin romper el flujo: si falla, queda un log de error sin datos."""
        if obtener_pool is None:
            return
        actor = getattr(quien, "user_id", None)
        try:
            async with admin_tx(obtener_pool(), actor) as conn:
                await registrar(conn, accion, "auth", actor, metadata)
        except Exception as e:
            _log.error("audit_auth_fallido", extra={"accion": accion, "error_tipo": type(e).__name__})

    async def login_fallido(codigo: str, error: ErrorSupabaseAuth | None) -> None:
        await auditar(AUDIT_LOGIN_FALLIDO, None, {"codigo": codigo})

    return ServiciosAuth(
        cliente=cliente,
        verificador=verificador,
        cookies=config.cookies_sesion,
        turnstile=turnstile or VerificadorTurnstile(config.turnstile_secret_key),
        config_ip=config.config_ip,
        por_ip=LimitadorIntentos(config.login_limite_ip, config.login_ventana_seg),
        por_email=LimitadorIntentos(config.login_limite_email, config.login_ventana_seg),
        por_ip_refresco=LimitadorIntentos(config.refresco_limite_ip, config.login_ventana_seg),
        nombre_totp=NOMBRE_TOTP,
        codigo_sin_acceso="sin_acceso_admin",
        mensaje_sin_acceso="Esta cuenta no es administradora de la plataforma",
        tiene_acceso=lambda identidad: identidad.is_platform_admin,
        exige_mfa=lambda identidad: True,
        auditar=auditar,
        cuerpo_sesion=_cuerpo_sesion,
        login_fallido=login_fallido,
    )
