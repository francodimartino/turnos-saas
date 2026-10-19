"""Capa de dominio de turnos: reglas del negocio de turnos que no dependen de ningún canal.

Qué vive acá: lo que significa "turno" y "hueco libre" para el negocio, sin saber quién lo pide. Hoy:

- `huecos`: lógica pura del cálculo de disponibilidad (reglas semanales - ocupados -> huecos), sin base de
  datos ni framework (`ReglaSemanal`, `Ventana`, `huecos_libres`).
- `consulta`: lectura de la base y `calcular_huecos(conn, profesional, servicio, rango)`. La conexión viene de
  `platform_tenancy.tenant_tx`: la RLS limita todo al tenant del contexto.
- `alta`: escritura de `bookings` compartida por los canales: `crear_turno` (un INSERT) y `mover_turno` (un
  UPDATE de `time_range`), con el lock por (tenant, profesional), el savepoint y la traducción del choque
  (23P01, 40P01, 40001). Lo que decide cada canal entra como parámetro explícito: `estado` y
  `pending_expires_at`, `booking_pack_id` y dos reglas opcionales que el canal activa (`limites`, con
  `LimitesReserva`, y `liberar_vencidos`). Los avisos los encola el canal, no el dominio.
- `limites`: `LimitesReserva`, sus defaults y `ReservaRechazada`.

La zona horaria del negocio no se define ni se reexporta acá: sale de `turnos_negocio.ZONA_HORARIA`, que es
su única definición.

Quién la usa: el bot (`bot_app.reserva`) y, a medida que se mueven, los demás canales (panel del dueño, jobs).

Regla de la capa: el dominio no importa `fastapi`, `bot_app`, `dashboard_backend` ni `admin_backend`
(lo verifica `tests/test_sin_dependencia_de_canales.py`). Cada canal decide qué reglas aplica sobre lo que el
dominio calcula (por ejemplo, el bot ofrece solo horarios futuros y el panel puede cargar uno a mano); el
dominio expone el cálculo, no la política de cada canal.

Documentación: ARCHITECTURE.md (mapa de paquetes y §7.1).
"""

from turnos_dominio.alta import Liberado, TurnoCreado, crear_turno, mover_turno
from turnos_dominio.consulta import calcular_huecos
from turnos_dominio.huecos import ReglaSemanal, Ventana, huecos_libres
from turnos_dominio.limites import LimitesReserva, ReservaRechazada

__all__ = [
    "LimitesReserva",
    "Liberado",
    "ReglaSemanal",
    "ReservaRechazada",
    "TurnoCreado",
    "Ventana",
    "calcular_huecos",
    "crear_turno",
    "huecos_libres",
    "mover_turno",
]
