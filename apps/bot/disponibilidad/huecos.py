"""Lógica pura del cálculo de huecos (ARCHITECTURE.md §7.1), sin acceso a la base.

huecos = ventanas de las `availability_rules` dentro del rango - ocupados (bookings no cancelados y
`availability_blocks`), partidas en turnos del largo del servicio (`duration_minutes`).

Zona horaria: las reglas son hora de pared (`time`) en `America/Argentina/Buenos_Aires` (§1.4). Se
convierten con `zoneinfo`, no con offsets fijos, y toda la aritmética se hace en UTC: así un cambio de
hora no desplaza ni duplica huecos.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

# datetime.UTC existe desde Python 3.11; timezone.utc también sirve en versiones anteriores.
UTC = timezone.utc  # noqa: UP017
_EPOCA = datetime(1970, 1, 1, tzinfo=UTC)


def _exigir_zona(valor: datetime, nombre: str) -> None:
    if valor.tzinfo is None or valor.utcoffset() is None:
        raise ValueError(f"{nombre} debe tener zona horaria (datetime aware)")


@dataclass(frozen=True)
class Ventana:
    """Intervalo semiabierto [inicio, fin) entre instantes con zona horaria."""

    inicio: datetime
    fin: datetime

    def __post_init__(self) -> None:
        _exigir_zona(self.inicio, "inicio")
        _exigir_zona(self.fin, "fin")


@dataclass(frozen=True)
class ReglaSemanal:
    """Una fila de `availability_rules`: `dia_semana` 0 = domingo ... 6 = sábado; horas de pared locales."""

    dia_semana: int
    hora_inicio: time
    hora_fin: time


def _fusionar(ventanas: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Ordena y une las ventanas que se superponen o se tocan."""
    resultado: list[tuple[datetime, datetime]] = []
    for inicio, fin in sorted(ventanas):
        if resultado and inicio <= resultado[-1][1]:
            resultado[-1] = (resultado[-1][0], max(resultado[-1][1], fin))
        else:
            resultado.append((inicio, fin))
    return resultado


def _restar(
    libres: list[tuple[datetime, datetime]], ocupados: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Resta `ocupados` de `libres`; ambas listas ya fusionadas y ordenadas."""
    resultado: list[tuple[datetime, datetime]] = []
    for inicio, fin in libres:
        cursor = inicio
        for o_inicio, o_fin in ocupados:
            if o_fin <= cursor or o_inicio >= fin:
                continue
            if o_inicio > cursor:
                resultado.append((cursor, o_inicio))
            cursor = max(cursor, o_fin)
            if cursor >= fin:
                break
        if cursor < fin:
            resultado.append((cursor, fin))
    return resultado


def _fechas(desde: datetime, hasta: datetime, zona: ZoneInfo) -> list[date]:
    primera, ultima = desde.astimezone(zona).date(), hasta.astimezone(zona).date()
    return [primera + timedelta(days=n) for n in range((ultima - primera).days + 1)]


def _hora_local_a_utc(dia: date, hora: time, zona: ZoneInfo) -> datetime:
    return datetime.combine(dia, hora, tzinfo=zona).astimezone(UTC)


def huecos_libres(
    reglas: list[ReglaSemanal],
    ocupados: list[Ventana],
    duracion_minutos: int,
    rango: Ventana,
    *,
    paso_minutos: int = 15,
    zona: ZoneInfo = ZONA_HORARIA,
    no_antes_de: datetime | None = None,
) -> list[Ventana]:
    """Huecos reservables de `duracion_minutos` dentro de `rango`, ordenados y en la zona `zona`.

    `no_antes_de` (anticipación mínima: ahora + N minutos) descarta los huecos que empiezan antes de ese instante;
    uno que empieza justo en él se conserva.

    Los inicios caen en una grilla de `paso_minutos` (alineada al reloj, no a cada ocupado), por lo que
    un hueco nunca empieza a las 10:07. Un hueco contiguo a un ocupado es válido: los intervalos son
    semiabiertos, igual que el `tstzrange` de `bookings` y su exclusion constraint.
    """
    if duracion_minutos <= 0:
        raise ValueError("duracion_minutos debe ser mayor que 0")
    if paso_minutos <= 0:
        raise ValueError("paso_minutos debe ser mayor que 0")
    desde, hasta = rango.inicio.astimezone(UTC), rango.fin.astimezone(UTC)
    if hasta <= desde:
        return []

    ventanas: list[tuple[datetime, datetime]] = []
    for dia in _fechas(desde, hasta, zona):
        dia_semana = (dia.weekday() + 1) % 7  # weekday(): lunes = 0; en la base domingo = 0
        for regla in reglas:
            if regla.dia_semana != dia_semana:
                continue
            inicio = max(_hora_local_a_utc(dia, regla.hora_inicio, zona), desde)
            fin = min(_hora_local_a_utc(dia, regla.hora_fin, zona), hasta)
            if inicio < fin:
                ventanas.append((inicio, fin))

    bloqueos = _fusionar([(o.inicio.astimezone(UTC), o.fin.astimezone(UTC)) for o in ocupados if o.inicio < o.fin])
    duracion = timedelta(minutes=duracion_minutos)
    paso = timedelta(minutes=paso_minutos)

    huecos: list[Ventana] = []
    for inicio, fin in _restar(_fusionar(ventanas), bloqueos):
        comienzo = _EPOCA + paso * -(-(inicio - _EPOCA) // paso)  # primer punto de la grilla >= inicio
        while comienzo + duracion <= fin:
            if no_antes_de is not None and comienzo < no_antes_de:
                comienzo += paso
                continue
            huecos.append(Ventana(comienzo.astimezone(zona), (comienzo + duracion).astimezone(zona)))
            comienzo += paso
    return huecos
