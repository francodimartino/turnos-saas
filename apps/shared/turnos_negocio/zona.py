"""Zona horaria del negocio (ARCHITECTURE.md §1.4): único lugar donde se define.

La importan el bot, el dashboard-backend y los jobs; ningún otro módulo de `apps/` ni de `platform/` puede crear
su propia zona (lo verifica un test estático). Si el huso cambiara, se cambia acá y las horas que ve el cliente
(bot, aviso) y el staff (dashboard) siguen coincidiendo.
"""

from zoneinfo import ZoneInfo

ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")
