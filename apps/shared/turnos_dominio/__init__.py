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
- `cambios`: cancelar y mover un turno por parte del cliente con el plazo del negocio (`cancelar_turno_cliente`,
  `mover_turno_cliente`), el cambio de horario pendiente de aprobación y su resolución (`aprobar_cambio_horario`,
  `rechazar_cambio_horario`), con errores propios (`ErrorDeTurno` y derivados) y `audit_log` en cada escritura.
- `avisos`: los avisos que se encolan en el outbox dentro de la transacción del cambio: `encolar_aviso_cliente`
  y `encolar_aviso_negocio` (con `AvisoInvalido` y sus derivados ante un dato inválido), `encolar_aviso_segun_canales`
  (lee los canales verificados de la ficha, encola y dice si corresponde el envío inmediato: solo con un único
  Telegram verificado) y `encolar_avisos_liberados` (vencimiento al liberar un horario) con `pedido_de_envio`. Encolar
  no envía ni resuelve destinatarios; el detalle está en el docstring del módulo.
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
from turnos_dominio.avisos import (
    AvisoCliente,
    AvisoInvalido,
    AvisoLiberado,
    CanalDeAvisoInvalido,
    DemoraDeAvisoInvalida,
    FichaParaAviso,
    InicioSinZona,
    PlantillaDeAvisoInvalida,
    encolar_aviso_cliente,
    encolar_aviso_negocio,
    encolar_aviso_segun_canales,
    encolar_avisos_liberados,
    leer_ficha_para_aviso,
    pedido_de_envio,
)
from turnos_dominio.cambios import (
    CambioNoPendiente,
    CambioResuelto,
    CambioVencido,
    CambioYaPendiente,
    ErrorDeTurno,
    HorarioOcupado,
    HorarioPasado,
    HorarioSinCambios,
    OperacionParaReintentar,
    PlazoVencido,
    TurnoCancelado,
    TurnoMovido,
    TurnoNoEncontrado,
    TurnoNoModificable,
    aprobar_cambio_horario,
    cancelar_turno_cliente,
    mover_turno_cliente,
    rechazar_cambio_horario,
)
from turnos_dominio.consulta import calcular_huecos
from turnos_dominio.huecos import ReglaSemanal, Ventana, huecos_libres
from turnos_dominio.limites import LimitesReserva, ReservaRechazada

__all__ = [
    "CambioNoPendiente",
    "CambioResuelto",
    "CambioVencido",
    "CambioYaPendiente",
    "ErrorDeTurno",
    "HorarioOcupado",
    "HorarioPasado",
    "HorarioSinCambios",
    "OperacionParaReintentar",
    "PlazoVencido",
    "TurnoCancelado",
    "TurnoMovido",
    "TurnoNoEncontrado",
    "TurnoNoModificable",
    "aprobar_cambio_horario",
    "cancelar_turno_cliente",
    "mover_turno_cliente",
    "rechazar_cambio_horario",
    "AvisoCliente",
    "AvisoInvalido",
    "AvisoLiberado",
    "CanalDeAvisoInvalido",
    "DemoraDeAvisoInvalida",
    "FichaParaAviso",
    "InicioSinZona",
    "LimitesReserva",
    "Liberado",
    "PackNoDisponible",
    "PlantillaDeAvisoInvalida",
    "ReglaSemanal",
    "ReservaRechazada",
    "TurnoCreado",
    "Ventana",
    "calcular_huecos",
    "crear_turno",
    "encolar_aviso_cliente",
    "encolar_aviso_negocio",
    "encolar_aviso_segun_canales",
    "encolar_avisos_liberados",
    "huecos_libres",
    "leer_ficha_para_aviso",
    "liberar_vencidos_solapados",
    "mover_turno",
    "pedido_de_envio",
]
