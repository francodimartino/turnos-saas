# Protección de `main`: no aplicable en GitHub Free (2026-10-04)

## Decisión
Franco decidió el 2026-10-04 seguir en GitHub Free sin branch protection (el repo es privado; la API lo exige en Pro o repo público). Se reconsidera contratando GitHub Pro: ahí se aplica la protección con los 6 checks de abajo como requeridos.

## Mitigación
- El único camino a `main` es la PR evaluada con CI verde.
- Auto-merge del repo desactivado (sin checks requeridos mergearía sin esperar al CI).
- Nunca push directo ni merge manual con checks en rojo.
- "Un test de aislamiento roto bloquea el merge" se cumple por proceso (revisión de la PR antes de mergear), no por GitHub.

## Salida real de la API (2026-10-04)
`gh api repos/francodimartino/turnos-saas/branches/main/protection`:
```
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","documentation_url":"https://docs.github.com/rest/branches/branch-protection#get-branch-protection","status":"403"}gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)

```
`gh api repos/francodimartino/turnos-saas/rulesets`:
```
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","documentation_url":"https://docs.github.com/rest/repos/rules#get-all-repository-rulesets","status":"403"}gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)

```

## Check runs del último commit de main (d21ad19c34e218225cbbd1c4c19a96c14af3dd93)
`gh api repos/francodimartino/turnos-saas/commits/<sha>/check-runs`:
```
Tests Python (pytest)	completed	success
Tests de integración Python (Supabase local)	in_progress	null
Tests pgTAP de aislamiento (Supabase local)	in_progress	null
Lint y typecheck (frontends) (dashboard-frontend)	completed	success
Lint y typecheck (frontends) (admin-frontend)	completed	success
Escaneo de secretos	completed	success
```
Los nombres coinciden con los jobs de `.github/workflows/ci.yml`.
