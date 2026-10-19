"""Capa de dominio de turnos: reglas del negocio de turnos que no dependen de ningún canal.

Qué vive acá: lo que significa "turno" y "hueco libre" para el negocio, sin saber quién lo pide. Hoy:

- `huecos`: lógica pura del cálculo de disponibilidad (reglas semanales - ocupados -> huecos), sin base de
  datos ni framework (`ReglaSemanal`, `Ventana`, `huecos_libres`).
- `consulta`: lectura de la base y `calcular_huecos(conn, profesional, servicio, rango)`. La conexión viene de
  `platform_tenancy.tenant_tx`: la RLS limita todo al tenant del contexto.

La zona horaria del negocio no se define ni se reexporta acá: sale de `turnos_negocio.ZONA_HORARIA`, que es
su única definición.

Quién la usa: el bot (`bot_app.reserva`) y, a medida que se mueven, los demás canales (panel del dueño, jobs).

Regla de la capa: el dominio no importa `fastapi`, `bot_app`, `dashboard_backend` ni `admin_backend`
(lo verifica `tests/test_sin_dependencia_de_canales.py`). Cada canal decide qué reglas aplica sobre lo que el
dominio calcula (por ejemplo, el bot ofrece solo horarios futuros y el panel puede cargar uno a mano); el
dominio expone el cálculo, no la política de cada canal.

Documentación: ARCHITECTURE.md (mapa de paquetes y §7.1).
"""

from turnos_dominio.consulta import calcular_huecos
from turnos_dominio.huecos import ReglaSemanal, Ventana, huecos_libres

__all__ = ["ReglaSemanal", "Ventana", "calcular_huecos", "huecos_libres"]
