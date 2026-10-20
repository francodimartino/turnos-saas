# turnos-saas

Gestión de turnos para estéticas y peluquerías (SaaS multi-tenant). El diseño completo está en [`ARCHITECTURE.md`](ARCHITECTURE.md); lo pendiente de definir, en [`PENDIENTESPORDEFINICION.md`](PENDIENTESPORDEFINICION.md). Convenciones de trabajo: [`AGENTS.md`](AGENTS.md).

## Estado

El proyecto ya tiene código de producto:

- `apps/`: `dashboard-backend` y `admin-backend` (FastAPI), `bot` (webhooks de Telegram y WhatsApp), `dashboard-frontend` y `admin-frontend` (Vite + React + TypeScript), `jobs` (purga diaria y vencimiento de turnos cada 15 minutos) y `shared` (código común).
- `platform/`: paquetes Python `platform_*` (tenancy, auth, secretos, admin, auditoría, notificaciones, calendario; el cobro está previsto, sin paquete todavía), templates SQL con sus tests pgTAP de aislamiento y la plantilla del frontend admin (copia de `apps/admin-frontend`, que es la fuente; se regenera con `python scripts/sincronizar_admin_frontend.py` y un test del CI falla si difieren).
- `supabase/migrations/`: migraciones SQL de la base (plataforma, dominio de turnos y las que conectan ambos).
- `tests/`: pytest (unitarios y de integración contra Supabase local); el CI además corre pgTAP, lint, tipos y el escaneo de secretos.

## Cómo seguir

- Probar la app completa en tu máquina (Docker Desktop): `python scripts/local.py up`; guía en [`docs/local.md`](docs/local.md).
- Verificar antes de abrir una PR: `python scripts/verificar.py tests` (ruff, mypy, pytest y tests de los frontends).
- Desplegar a producción: [`docs/deploy.md`](docs/deploy.md).
