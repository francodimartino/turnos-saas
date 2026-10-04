# AGENTS.md — turnos-saas

Convenciones del proyecto. La fuente de verdad del diseño es `ARCHITECTURE.md`; las decisiones abiertas están en `PENDIENTESPORDEFINICION.md`. Si algo de acá contradice a `ARCHITECTURE.md`, manda `ARCHITECTURE.md`.

## Qué es

SaaS multi-tenant de gestión de turnos para estéticas y peluquerías. Tres niveles de actores (plataforma, tenant, cliente final). Escala objetivo: 10-50 tenants. Un solo developer, part-time. Idioma del proyecto (documentos, comentarios, commits, PRs): español. Zona horaria única: `America/Argentina/Buenos_Aires`; moneda ARS.

## Stack

- Backend: Python 3.11+ con FastAPI. Acceso a datos con `asyncpg` (conexión directa a Postgres, no el cliente de Supabase), dentro de una transacción explícita por request.
- Frontend: Vite + React + TypeScript (SPA, sin SSR). Sin paquete de componentes compartido entre frontends.
- DB/Auth/Storage: Supabase, un proyecto por producto. Dev con Supabase local (CLI + Docker); prod hosteado; sin staging.
- Hosting: Vercel (frontends), Render (backends y cron jobs), Cloudflare (DNS y proxy/WAF). Errores: Sentry.
- Tests: `pytest` (Python), pgTAP (aislamiento RLS), lint/typecheck de TypeScript en cada frontend.

## Estructura

```
platform/                 Paquetes Python platform_* (tenancy, auth, secrets, admin, audit, notifications, calendar, payments, billing)
  infra/                  Templates SQL (RLS) y tests pgTAP
  admin-frontend-template/  Template de frontend admin, copiado una vez a apps/admin-frontend
apps/
  dashboard-frontend/     Vite+React (Vercel)
  dashboard-backend/      FastAPI (Render), usa platform_*
  admin-frontend/         Copia del template (Vercel, deploy separado)
  admin-backend/          FastAPI (Render, deploy separado), usa platform_admin
  bot/                    FastAPI: webhooks Telegram/WhatsApp y Mercado Pago (Render)
  shared/turnos_queries/  Consultas compartidas entre dashboard-backend y admin-backend
  jobs/                   Render Cron Jobs (purga diaria, vencimiento cada 15 min)
supabase/migrations/      Migraciones SQL (plataforma, luego dominio turnos, luego las que conectan ambos)
```

## Reglas que no se negocian

1. **Aislamiento por defecto**: RLS es el límite real, nunca la única capa. Toda tabla con datos de tenant lleva policy RLS y test pgTAP de aislamiento; un test de aislamiento roto bloquea el merge.
2. **Cero confianza en el cliente**: `tenant_id`, claims e identidad nunca se toman del input sin verificar del lado servidor.
3. **Todo enum-como-texto** lleva `check (... in (...))` y `not null` en la base.
4. **Las apps no usan el SDK de Supabase directamente**: pasan por las interfaces de `platform_*` (portabilidad).
5. **Sin secretos en el repo**: credenciales en variables de entorno / GitHub Secrets / Supabase Vault. `.env` nunca se commitea (`.env.example` sí, sin valores).
6. **No sobre-diseñar**: no extraer código compartido sin evidencia de un segundo uso; no inventar dependencias no mencionadas en `ARCHITECTURE.md`.
7. **Logs estructurados (JSON)** con `tenant_id`/`actor_type` cuando aplique; sin PII.
8. Cada backend FastAPI expone `GET /health` (200 solo si el servicio y su conexión a Supabase están bien).

## Comandos

- Tests Python: `pytest`
- Lint Python: `ruff check .`
- Verificación local completa (pytest + pgTAP si Supabase local está levantado): `python scripts/verificar.py tests`; frontends: `python scripts/verificar.py build`
- Instalar para desarrollo: `pip install -e ".[dev]"`
- Escaneo de secretos (diff staged): `bash .github/scripts/escanear-secretos.sh`
- Tests de aislamiento: `supabase start` y `supabase test db` (la CLI solo ve `supabase/tests/`: antes se copian ahí los `.sql` de `platform/infra/pgtap-tests/` con prefijo `platform_`, como hacen el CI y `scripts/verificar.py`)
- Frontends (cuando tengan `package.json`): `npm run lint`, `npm run typecheck`, `npm test`

## Flujo de trabajo

- Todo cambio entra a `main` por PR con CI en verde (secretos, pytest, frontends, pgTAP, integración). El repo no tiene branch protection (cuenta GitHub Free, repo privado), así que la regla se cumple por disciplina: nunca push directo a `main`, nunca merge con algún check en rojo, auto-merge del repo desactivado. Checks: `Escaneo de secretos`, `Tests Python (pytest)`, `Lint y typecheck (frontends) (admin-frontend)`, `Lint y typecheck (frontends) (dashboard-frontend)`, `Tests pgTAP de aislamiento (Supabase local)`, `Tests de integración Python (Supabase local)`.
- Commits y PRs en español, mensajes cortos que expliquen el porqué.
- Migraciones: se copian una vez desde `platform/infra/` a `supabase/migrations/` y divergen; las mejoras se aplican a mano.
