"""Configuración del enviador desde el entorno, validada entera al arrancar (ARCHITECTURE.md §2.12, §3.2).

El enviador corre siempre con el rol `enviador`. Variables:

- `ENVIADOR_DATABASE_URL` (obligatoria): conexión como `enviador`.
- `RESEND_API_KEY` y `EMAIL_REMITENTE` (obligatorias): cuenta de Resend y remitente `@agendemos.ar`.
- `SENTRY_DSN` (opcional): DSN del proyecto `turnos-enviador`; sin DSN no hay Sentry.
- `TELEGRAM_API_BASE_URL` (opcional): solo para un simulador local (`platform_notifications.base_url_api`).
- `ENVIADOR_INTERVALO_SEG` (2): espera entre reclamos cuando hay trabajo; sin trabajo se duplica hasta
  `ENVIADOR_ESPERA_MAXIMA_SEG` (10).
- `ENVIADOR_CONCURRENCIA` (5) filas en simultáneo dentro de un lote de `ENVIADOR_LOTE` (10).
- `ENVIADOR_PLAZO_FILA_SEG` (120): tope de una fila; `ENVIADOR_PLAZO_APAGADO_SEG` (20): lo que se espera a las filas en
  curso al recibir SIGTERM.
- `OUTBOX_ARRIENDO_SEG` (300): lo que una fila reclamada queda fuera del alcance de otra instancia y del job de
  vencimiento; también la espera antes de reintentar una fila reprogramada. `OUTBOX_MAX_INTENTOS` (5): reclamos
  antes de dejarla `failed`.

Un valor ausente toma el defecto; uno inválido o fuera de rango impide arrancar, con un mensaje que nombra la
variable y nunca su valor.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from platform_notifications import base_url_api
from turnos_config import ConfigInvalida, obligatoria, remitente_email, texto_opcional
from turnos_email import ConfigEmail

ROL = "enviador"
SERVICIO = "turnos-enviador"
VARIABLE_DSN = "ENVIADOR_DATABASE_URL"


@dataclass(frozen=True)
class Config:
    dsn: str = field(repr=False)
    sentry_dsn: str | None = field(repr=False)
    email: ConfigEmail
    base_url_telegram: str
    intervalo_seg: float
    espera_maxima_seg: float
    concurrencia: int
    lote: int
    plazo_fila_seg: float
    plazo_apagado_seg: float
    arriendo_seg: int
    max_intentos: int

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        intervalo = _decimal(entorno, "ENVIADOR_INTERVALO_SEG", 2.0, 0.1, 60.0)
        concurrencia = _entero(entorno, "ENVIADOR_CONCURRENCIA", 5, 1, 50)
        lote = _entero(entorno, "ENVIADOR_LOTE", 10, 1, 500)
        plazo_fila = _decimal(entorno, "ENVIADOR_PLAZO_FILA_SEG", 120.0, 10.0, 3600.0)
        arriendo = _entero(entorno, "OUTBOX_ARRIENDO_SEG", 300, 30, 86400)
        # Peor caso de un lote: cada tanda de `concurrencia` filas tarda hasta `plazo_fila`. Si el arriendo vence antes
        # de que termine la última, otra instancia (o el job de vencimiento) reclamaría una fila todavía en envío.
        peor_caso = plazo_fila * math.ceil(lote / concurrencia)
        if arriendo <= peor_caso:
            raise ConfigInvalida(
                f"OUTBOX_ARRIENDO_SEG debe superar {peor_caso:g} s (ENVIADOR_PLAZO_FILA_SEG por las tandas del lote)"
            )
        return cls(
            dsn=_dsn_del_enviador(obligatoria(entorno, VARIABLE_DSN)),
            sentry_dsn=texto_opcional(entorno, "SENTRY_DSN") or None,
            email=ConfigEmail(
                api_key=obligatoria(entorno, "RESEND_API_KEY"),
                remitente=remitente_email(entorno, "EMAIL_REMITENTE"),
                max_reintentos=0,  # los reintentos los decide la política del outbox, no el cliente de email
            ),
            base_url_telegram=_base_url_telegram(entorno),
            intervalo_seg=intervalo,
            espera_maxima_seg=_decimal(entorno, "ENVIADOR_ESPERA_MAXIMA_SEG", max(10.0, intervalo), intervalo, 300.0),
            concurrencia=concurrencia,
            lote=lote,
            plazo_fila_seg=plazo_fila,
            plazo_apagado_seg=_decimal(entorno, "ENVIADOR_PLAZO_APAGADO_SEG", 20.0, 1.0, 120.0),
            arriendo_seg=arriendo,
            max_intentos=_entero(entorno, "OUTBOX_MAX_INTENTOS", 5, 2, 20),
        )


def _dsn_del_enviador(dsn: str) -> str:
    """Rechaza una conexión que no sea del rol `enviador` (o `enviador.<ref>`, el usuario del pooler de Supabase).

    La garantía real es `verificar_rol`, que comprueba el rol efectivo en la base al arrancar y en cada `/health`.
    """
    usuario = urlsplit(dsn).username
    if usuario is None or unquote(usuario).split(".", 1)[0] != ROL:
        raise ConfigInvalida(f"{VARIABLE_DSN} debe conectar con el rol {ROL}")
    return dsn


def _base_url_telegram(entorno: Mapping[str, str]) -> str:
    try:
        return base_url_api(entorno)
    except ValueError:
        raise ConfigInvalida("TELEGRAM_API_BASE_URL solo admite http hacia una máquina local") from None


def _decimal(entorno: Mapping[str, str], nombre: str, defecto: float, minimo: float, maximo: float) -> float:
    crudo = texto_opcional(entorno, nombre)
    if not crudo:
        return defecto
    try:
        valor = float(crudo)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un número") from None
    if not math.isfinite(valor) or not minimo <= valor <= maximo:
        raise ConfigInvalida(f"{nombre} debe estar entre {minimo:g} y {maximo:g}")
    return valor


def _entero(entorno: Mapping[str, str], nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    crudo = texto_opcional(entorno, nombre)
    if not crudo:
        return defecto
    try:
        valor = int(crudo)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un entero") from None
    if not minimo <= valor <= maximo:
        raise ConfigInvalida(f"{nombre} debe estar entre {minimo} y {maximo}")
    return valor
