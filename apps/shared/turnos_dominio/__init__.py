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
  `LimitesReserva`, y `liberar_vencidos`). `liberar_vencidos_solapados` es la única liberación de pendientes
  vencidos.
- `avisos`: el aviso de vencimiento al cliente liberado, común a los canales que liberan:
  `encolar_avisos_liberados` lo encola en el outbox dentro de la transacción de la reserva y `pedido_de_envio`
  arma el envío posterior al commit. Los demás avisos los encola el canal.
- `limites`: `LimitesReserva`, sus defaults y `ReservaRechazada`.

La zona horaria del negocio no se define ni se reexporta acá: sale de `turnos_negocio.ZONA_HORARIA`, que es
su única definición.

Quién la usa: el bot (`turnos_bot.reserva`) y, a medida que se mueven, los demás canales (panel del dueño, jobs).

Regla de la capa: el dominio no importa `fastapi`, `starlette`, `turnos_bot`, `dashboard_backend` ni `admin_backend`
(lo hace cumplir ruff con `apps/shared/turnos_dominio/ruff.toml`; lo prueba
`tests/test_imports_prohibidos.py`). Cada canal decide qué reglas aplica sobre lo que el
dominio calcula (por ejemplo, el bot ofrece solo horarios futuros y el panel puede cargar uno a mano); el
dominio expone el cálculo, no la política de cada canal.

Documentación: ARCHITECTURE.md (mapa de paquetes y §7.1).
"""

from turnos_dominio.alta import (
    Liberado,
    PackNoDisponible,
    TurnoCreado,
    crear_turno,
    liberar_vencidos_solapados,
    mover_turno,
)
from turnos_dominio.avisos import AvisoLiberado, encolar_avisos_liberados, pedido_de_envio
from turnos_dominio.consulta import calcular_huecos
from turnos_dominio.huecos import ReglaSemanal, Ventana, huecos_libres
from turnos_dominio.limites import LimitesReserva, ReservaRechazada

__all__ = [
    "AvisoLiberado",
    "LimitesReserva",
    "Liberado",
    "PackNoDisponible",
    "ReglaSemanal",
    "ReservaRechazada",
    "TurnoCreado",
    "Ventana",
    "calcular_huecos",
    "crear_turno",
    "encolar_avisos_liberados",
    "huecos_libres",
    "liberar_vencidos_solapados",
    "mover_turno",
    "pedido_de_envio",
]
