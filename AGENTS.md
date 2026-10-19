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
- Lint Python: `ruff check .` (reglas E, F, I, UP, B, ASYNC, S, SIM, RET y PTH, en `[tool.ruff.lint]` de `pyproject.toml`; mismo comando en el CI y en `scripts/verificar.py`). Excepciones: `per-file-ignores` acotados en `pyproject.toml` con su motivo, o `# noqa: REGLA - motivo` en la línea; nada de ignores globales sin motivo
- Tipos Python (mypy en modo básico, configurado en `pyproject.toml`): `mypy .`
- Verificación local completa (ruff + mypy + pytest + `npm test` de cada frontend + pgTAP si Supabase local está levantado): `python scripts/verificar.py tests`; frontends (typecheck y build): `python scripts/verificar.py build`. Ambos corren `npm ci` solos en cada frontend con `package.json` si falta `node_modules` o cambió `package-lock.json` (copia de trabajo nueva sin pasos manuales). Un frontend nuevo solo se agrega a `FRONTENDS` en `scripts/verificar.py` y a la matriz del CI.
- Entorno local completo (Supabase local + 3 backends + 2 frontends + jobs + simulador de Telegram, en Docker, con datos de demo): `python scripts/local.py up` / `down` / `reset` (guía en `docs/local.md`). Puertos propios (55420-55424, 8110-8112, 8120, 5173-5174); `.env.local` y `.local/` no se versionan. `TELEGRAM_API_BASE_URL` (solo `http` hacia localhost) hace que el adapter de Telegram use el simulador en vez de `api.telegram.org`; sin definir, usa el oficial.
- Setup inicial del proyecto Supabase (una vez, tras migrar y crear en Auth la cuenta del dueño): `SETUP_DATABASE_URL=<conexión directa como dueño de las migraciones> python scripts/setup_proyecto.py --email <email>` (o `--auth-user-id <uuid>`). Inserta el primer `platform_admins` y siembra en Vault la clave `channel_user_hmac_key` (solo si no existe: nunca la rota ni la imprime). Contraseñas de los 6 roles de login: variables `SETUP_PASSWORD_<ROL>` o `--pedir-passwords` (prompt sin eco); nunca por argumento. El App Secret de WhatsApp de producción queda diferido con el BSP.
- Despliegue (Render `render.yaml`, Vercel `vercel.json`, variables por servicio, roles de conexión, orden del primer deploy): `docs/deploy.md`. El deploy no es automático al mergear.
- Instalar para desarrollo (Python 3.12, igual que Render y el CI): `pip install -r requirements-dev.lock && pip install --no-deps -e .`. `requirements.lock` es solo runtime (lo instala Render, sin herramientas de desarrollo) y `requirements-dev.lock` es runtime + dev con las mismas versiones. Una dependencia nueva se declara en `pyproject.toml` y se regeneran los dos en el mismo PR, primero el de runtime: `uv pip compile pyproject.toml --python-version 3.12 --python-platform linux --generate-hashes --no-header -o requirements.lock` y después `uv pip compile pyproject.toml --extra dev -c requirements.lock --python-version 3.12 --python-platform linux --generate-hashes --no-header -o requirements-dev.lock` (detalle en `docs/deploy.md`, "Lock de dependencias").
- Los tests no requieren instalar: `[tool.pytest.ini_options] pythonpath` en `pyproject.toml` lista la raíz de cada paquete (`platform/packages/platform_*`, `apps/shared`, `apps/bot`). **Un paquete nuevo se agrega ahí y en `[tool.setuptools.packages.find].where`.**
- Escaneo de secretos (diff staged): `bash .github/scripts/escanear-secretos.sh`
- Tests de aislamiento: `supabase start` y `supabase test db` (la CLI solo ve `supabase/tests/`: antes se copian ahí los `.sql` de `platform/infra/pgtap-tests/` con prefijo `platform_`, como hacen el CI y `scripts/verificar.py`)
- Frontends (cuando tengan `package.json`): `npm run lint`, `npm run typecheck`, `npm test`

## Flujo de trabajo

- Todo cambio entra a `main` por PR con CI en verde (secretos, pytest, frontends, pgTAP, integración). El repo no tiene branch protection (cuenta GitHub Free, repo privado; decisión de Franco del 2026-10-04, se reconsidera con GitHub Pro), así que la regla se cumple por proceso (el evaluador solo mergea con CI verde) y no por GitHub; un test de aislamiento roto sigue bloqueando el merge por esa vía: nunca push directo a `main`, nunca merge con algún check en rojo, auto-merge del repo desactivado. Checks: `Escaneo de secretos`, `Tests Python (pytest)`, `Lint y typecheck (frontends) (admin-frontend)`, `Lint y typecheck (frontends) (dashboard-frontend)`, `Tests pgTAP de aislamiento (Supabase local)`, `Tests de integración Python (Supabase local)`.
- Comportamiento del CI (`.github/workflows/ci.yml`, GitHub Free = 2.000 min/mes de Actions): corre en PRs y a mano (Actions > CI > Run workflow, suite completa); **no** en push a `main` (la corrida de la PR ya probó el merge con `main` y todavía no hay deploy automático; si se agrega deploy, reevaluar). Un push nuevo a la misma PR cancela la corrida anterior (`concurrency`). Todos los jobs tienen `timeout-minutes`. Un job previo, `Detectar cambios`, mira los archivos de la PR y decide: `Tests pgTAP…` e `integración…` corren si cambió `supabase/`, `platform/` (salvo `admin-frontend-template`), `apps/` (salvo los frontends), `tests/`, `conftest.py`, `pyproject.toml`, `scripts/verificar.py` o el CI; los dos checks de frontends corren si cambió `apps/*-frontend/`, `platform/admin-frontend-template/`, algún `package.json`/`package-lock.json` o el CI. Un job que no aplica queda `skipped` (los de frontends terminan en `success` con un paso que lo avisa: los checks de matriz saltados no conservarían su nombre). `Escaneo de secretos` y `Tests Python (pytest)` corren siempre. Los pasos que levantan Supabase usan `.github/scripts/supabase-start.sh`: solo base y auth, hasta 3 intentos (por el error esporádico `toomanyrequests: Rate exceeded` al bajar imágenes) y login opcional al registro si existen los secrets `DOCKERHUB_USERNAME` y `DOCKERHUB_TOKEN` (no son obligatorios). Si algo depende de otro servicio de Supabase (storage, realtime, postgrest…), se quita de la lista `EXCLUIR` del script.
- Commits y PRs en español, mensajes cortos que expliquen el porqué.
- Migraciones: se copian una vez desde `platform/infra/` a `supabase/migrations/` y divergen; las mejoras se aplican a mano.
