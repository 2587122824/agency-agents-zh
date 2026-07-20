# Agency Studio V2

V2 is an isolated modular application. It does not import or change the V1
workflow runtime. Stable V1 provider adapters can be connected later through
explicit V2 production interfaces.

## Stack

- React, TypeScript, Vite, React Router, TanStack Query, Zustand
- FastAPI, Pydantic v2, SQLAlchemy 2, Alembic
- SQLite for local development
- Database-backed work queue and server-sent events

## Run locally

```powershell
python -m pip install -r v2/backend/requirements.txt
cd v2/frontend
npm.cmd install
npm.cmd run build
cd ../..
python -m uvicorn v2.backend.app.main:app --host 127.0.0.1 --port 8766
```

Run the worker in another terminal when queued work should be processed:

```powershell
python -m v2.backend.app.workers.worker
```

Open <http://127.0.0.1:8766>. V1 remains available on port `8765`.

After installing dependencies and building once, Windows users can start the
API and Worker together with:

```powershell
v2\start_v2.bat -NoBrowser
```

## RunningHub connection preparation

RunningHub secrets stay in the backend process environment and are never
entered in the web settings page. Before starting V2, use a newly rotated key:

```powershell
$env:V2_CREDENTIAL_ENV_ALLOWLIST = "RUNNINGHUB_API_KEY"
$env:RUNNINGHUB_API_KEY = "<new key>"
v2\start_v2.bat -NoBrowser
```

For a persistent local installation, store the same three values in the
Windows user environment. `start_v2.ps1` merges the process and user credential allowlists,
allowlisted credentials, agent execution switch, and external provider
execution switch into the API and Worker processes on every restart. Secrets
must not be committed to the repository or stored in the production
configuration database.

The published Provider configuration must reference
`env://RUNNINGHUB_API_KEY`. The settings page then checks the adapter,
configuration contract, credential, and execution authorization without a
network request. Set `V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED=true` only after
the user explicitly authorizes real external production. Enabling it does not
retry historical work items or submit a provider request by itself. See
`docs/V2_PROVIDER_CONNECTION_READINESS_IMPLEMENTATION.md` for the complete
boundary.

## Boundaries

- `contracts/`: API and domain contracts. Missing fields fail validation.
- `projects/`: project lifecycle rules.
- `decisions/`: append-only user decision ledger.
- `events/`: persisted event stream used by SSE.
- `workers/`: explicit work-item execution.
- `quality/`: verified provider outputs, deterministic QC evidence, and explicit human review.
- `editor/`: immutable timeline candidates, deterministic reference validation, and explicit confirmation.
- `providers/`: strict provider contracts and independent adapters; RunningHub
  image and first-frame-video execution is registered but disabled by default.

No automatic retry, route substitution, prompt rewriting, output repair, or
provider downgrade belongs in this foundation.
