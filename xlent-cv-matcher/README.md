# XLENT CV Matcher - MVP scaffold

Denne mappen inneholder:
- `apps/api`: FastAPI-backend med SQLite, import, analyse, CV-varianter og forslag.
- `apps/web`: React/Vite frontend som kjører en enkel ende-til-ende MVP-flyt.

## 1) Kjør API lokalt

```bash
cd apps/api
python -m venv .venv
.venv\Scripts\activate
pip install -e .
uvicorn app.main:app --reload
```

Swagger: `http://127.0.0.1:8000/docs`

Miljøvariabler (valgfritt for AI):
- `OPENAI_API_KEY`
- `USE_OPENAI_ANALYSIS=true`
- `OPENAI_MODEL=gpt-4.1-mini`
- `OPENAI_ALLOWED_MODELS=gpt-4.1-mini,gpt-4.1,gpt-4o-mini,gpt-4o,o4-mini`
- `CINODE_BASE_URL`
- `CINODE_API_TOKEN`
- `CINODE_PUBLISH_PATH` (default `api/cv-import`)
- `ENABLE_CINODE_PUBLISH=true`

Uten disse brukes heuristisk fallback for analyse/forslag.
`publish/cinode` støtter også `dry_run=true` for lokal test uten kall.

## 2) Kjør web lokalt

```bash
cd apps/web
npm install
npm run dev
```

Frontend: `http://127.0.0.1:5173`

## Start/stop scripts (Windows PowerShell)

Fra prosjektroten:

```powershell
.\start-local.ps1
```

Rask oppstart uten å vente på port-check:

```powershell
.\start-local.ps1 -NoWait
```

Stopper alt igjen:

```powershell
.\stop-local.ps1
```

Notat: Hvis `npm` ikke finnes i PATH, starter skriptet kun API og gir varsel.

## Installer på ny maskin (Windows PowerShell)

Fra prosjektroten:

```powershell
.\install-on-new-machine.ps1
```

Nyttige flagg:

```powershell
.\install-on-new-machine.ps1 -StartAfterInstall
.\install-on-new-machine.ps1 -StartAfterInstall -NoWait
.\install-on-new-machine.ps1 -SkipBuildCheck
```

Skriptet gjør følgende:
- sjekker `python`, `node`, `npm`
- lager `apps/api/.venv` ved behov
- installerer API-avhengigheter og Playwright Chromium
- installerer web-avhengigheter
- kjører `npm run build` (kan hoppes over med `-SkipBuildCheck`)

## Kopier siste versjon til Git

Fra `xlent-cv-matcher`:

```powershell
.\copy_to_git.ps1
```

Valgfrie flagg:

```powershell
.\copy_to_git.ps1 -NoPush
.\copy_to_git.ps1 -CommitMessage "Oppdatering CV matcher"
.\copy_to_git.ps1 -IncludeRepoRootFiles
.\copy_to_git.ps1 -TargetBranch main
```

Standard remote i scriptet er:
`origin -> https://github.com/telboth/Cinode_cv_matcher.git`

## MVP-endepunkter

- `POST /api/v1/employees`
- `GET /api/v1/employees/{employee_id}`
- `POST /api/v1/sources/cinode/import`
- `POST /api/v1/sources/docx/import`
- `GET /api/v1/sources/profiles/{employee_id}/latest`
- `POST /api/v1/opportunities`
- `POST /api/v1/opportunities/{opportunity_id}/analyze`
- `GET /api/v1/opportunities/{opportunity_id}/requirements`
- `GET /api/v1/config/openai-models`
- `GET /api/v1/cinode/credentials`
- `POST /api/v1/cinode/credentials`
- `POST /api/v1/cinode/credentials/{credential_id}/test`
- `POST /api/v1/cinode/credentials/{credential_id}/set-default`
- `DELETE /api/v1/cinode/credentials/{credential_id}`
- `POST /api/v1/cinode/credentials/{credential_id}/consultants`
- `POST /api/v1/cv-variants`
- `GET /api/v1/cv-variants/{variant_id}`
- `POST /api/v1/cv-variants/{variant_id}/suggest`
- `GET /api/v1/cv-variants/{variant_id}/suggestions`
- `PATCH /api/v1/cv-variants/{variant_id}/suggestions/{suggestion_id}`
- `POST /api/v1/cv-variants/{variant_id}/export/cinode-payload`
- `POST /api/v1/cv-variants/{variant_id}/publish/cinode`

`analyze` og `suggest` kan ta valgfri body: `{ "model_override": "gpt-4.1" }`.
`publish/cinode` kan ta valgfri `credential_id` for å publisere med valgt Cinode-token.
