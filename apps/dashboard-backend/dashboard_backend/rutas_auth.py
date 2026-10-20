"""Endpoints `/auth/*` del dashboard (ARCHITECTURE.md §3.1.5, §3.6).

Login, MFA (enrolar y verificar), refrescar, logout y sesión los define una sola vez `turnos_sesion.rutas_auth`
(límites de intentos por IP y por email, Turnstile, cookies, auditoría); acá solo se arma lo propio del dashboard y
se suma un endpoint:

  POST /auth/invitacion/aceptar   token_hash del mail de invitación + contraseña nueva -> sesión; cuenta intentos
                                  por IP, exige Turnstile (si está activo) y deja `auth_invitation_accepted`.

Lo propio del dashboard dentro de las rutas compartidas (`servicios_auth`):
  - Solo entra quien tiene un negocio (`tenant_id`): sin él, 403 `sin_acceso_dashboard` y sin cookies.
  - Exige MFA el owner y el admin de plataforma; el resto sigue con `siguiente_paso: listo` si no tiene factor.
  - La auditoría escribe en el tenant de la sesión, con el usuario como actor. Es de mejor esfuerzo: si falla, se
    loguea sin PII y el flujo sigue. Sin tenant (Franco o staff desactivado) no hay a qué local atribuirla.
  - El login FALLIDO no va a `audit_log`: un intento con un email cualquiera no pertenece a ningún local, y abrir la
    escritura sin local permitiría llenar la tabla desde afuera. Queda solo como log estructurado
    (`auth_login_failed`, sin email, IP ni token) y en los logs de Supabase Auth.
  - `GET /auth/sesion` devuelve user_id, tenant_id, is_owner, aal y los permisos vivos.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from platform_audit import registrar
from platform_auth import ErrorAuth, VerificadorJWT, listar_permisos_vivos
from platform_tenancy import tenant_tx
from pydantic import BaseModel, ConfigDict, Field
from turnos_http import http_desde_error_auth
from turnos_proteccion_login import LimitadorIntentos, VerificadorTurnstile
from turnos_sesion import ServiciosAuth
from turnos_sesion.respuestas_auth import SiguientePasoOut
from turnos_sesion.rutas_auth import (
    auditar_con_tokens,
    exigir_turnstile,
    limitar_ip,
    servicios_de,
    siguiente_paso,
)

from dashboard_backend.config import Config
from dashboard_backend.dependencias import DependenciasDashboard, dependencias_de
from dashboard_backend.supabase_auth import ClienteSupabaseAuth, CredencialesInvalidas, ErrorSupabaseAuth

_log = logging.getLogger("dashboard_backend.auth")
NOMBRE_TOTP = "Turnos"


class AceptarInvitacionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token_hash: str = Field(min_length=1, max_length=2048)
    password: str = Field(min_length=1, max_length=1024)
    turnstile_token: str | None = Field(default=None, max_length=4096)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/invitacion/aceptar", response_model=SiguientePasoOut, response_model_exclude_unset=True)
async def aceptar_invitacion(datos: AceptarInvitacionIn, request: Request, respuesta: Response) -> Any:
    s = servicios_de(request)
    ip = limitar_ip(request)
    await exigir_turnstile(request, datos.turnstile_token, ip)
    tokens = await s.cliente.aceptar_invitacion(datos.token_hash, datos.password)
    cuerpo = {**siguiente_paso(request, tokens), "expira_en": tokens.expires_in}
    s.cookies.poner(respuesta, tokens)
    await auditar_con_tokens(request, "auth_invitation_accepted", tokens)
    return cuerpo


async def _cuerpo_sesion(request: Request) -> dict[str, Any]:
    deps = dependencias_de(request)
    identidad = await deps.identidad(request)
    async with deps.abrir_sesion(identidad) as tx:
        s = tx.sesion
        # Los permisos se leen en vivo en cada request (nunca del JWT): el frontend filtra la navegación con ellos.
        try:
            permisos = await listar_permisos_vivos(tx.conn, s)
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None
        return {
            "user_id": str(s.user_id),
            "tenant_id": str(s.tenant_id),
            "is_owner": s.is_owner,
            "aal": s.aal,
            "permisos": permisos,
        }


def servicios_auth(
    config: Config,
    cliente: ClienteSupabaseAuth,
    deps: DependenciasDashboard,
    verificador: VerificadorJWT,
    turnstile: VerificadorTurnstile | None = None,
) -> ServiciosAuth:
    """Lo que el dashboard parametriza en las rutas compartidas; `crear_app` lo deja en `app.state.auth`."""
    limites = config.login_limites

    async def auditar(accion: str, quien: Any, metadata: dict[str, Any] | None = None) -> None:
        """Escribe una fila de audit de autenticación en el tenant de `quien`. Nunca rompe el flujo."""
        tenant_id: uuid.UUID | None = getattr(quien, "tenant_id", None)
        if tenant_id is None:
            return
        try:
            async with tenant_tx(deps.obtener_pool(), tenant_id, quien.user_id) as conn:
                await registrar(conn, accion, "auth", quien.user_id, metadata)
        except Exception as e:  # noqa: BLE001 - el audit es de mejor esfuerzo; el detalle puede nombrar datos
            _log.error("audit_auth_fallo", extra={"codigo": accion, "error_tipo": type(e).__name__})

    async def login_fallido(codigo: str, error: ErrorSupabaseAuth | None) -> None:
        if isinstance(error, CredencialesInvalidas):
            _log.warning("auth_login_failed", extra={"motivo": "credenciales_invalidas"})

    return ServiciosAuth(
        cliente=cliente,
        verificador=verificador,
        cookies=config.cookies_sesion,
        turnstile=turnstile or VerificadorTurnstile(config.turnstile_secret),
        config_ip=config.config_ip,
        por_ip=LimitadorIntentos(limites.por_ip, limites.ventana_seg),
        por_email=LimitadorIntentos(limites.por_email, limites.ventana_seg),
        por_ip_refresco=LimitadorIntentos(config.refresco_limite_ip, limites.ventana_seg),
        nombre_totp=NOMBRE_TOTP,
        codigo_sin_acceso="sin_acceso_dashboard",
        mensaje_sin_acceso=(
            "Esta cuenta no tiene acceso a ningún negocio. "
            "Si administrás la plataforma, entrá por el panel de administración."
        ),
        # Franco o staff desactivado (sin tenant): no es una sesión de dashboard y no se le entregan cookies.
        tiene_acceso=lambda identidad: identidad.tenant_id is not None,
        exige_mfa=lambda identidad: identidad.is_owner or identidad.is_platform_admin,
        auditar=auditar,
        cuerpo_sesion=_cuerpo_sesion,
        login_fallido=login_fallido,
    )
