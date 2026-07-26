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

## API provider configuration

In the current local single-user phase, every API provider stores its API Key
as a normal field in the versioned system configuration. Open the settings
page, edit the current configuration, fill the provider's API address and API
Key, then validate and publish the new version. RunningHub, CosyVoice and all text-agent
gateways read the exact published provider version; there is no environment
variable fallback.

Configuration v54 includes one explicit CosyVoice preset route
(`cosyvoice-v1 / longxiaochun / 24000Hz WAV`). It remains unavailable until
the DashScope API Key is filled and a new configuration version is validated
and published. Voice cloning is not part of this route.

The readiness panel checks whether the API Key field is filled without making
a network request. This does not prove that the key is valid or that the
provider is reachable. Set `V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED=true` only
after the user explicitly authorizes real external production. Enabling it
does not retry historical work items or submit a provider request by itself.

Plain API Key persistence and API round-tripping are accepted only for the
local single-user development environment. Do not commit keys to Git or expose
this configuration API on a multi-user or public deployment.

## Boundaries

- `contracts/`: API and domain contracts. Missing fields fail validation.
- `projects/`: project lifecycle rules.
- `decisions/`: append-only user decision ledger.
- `events/`: persisted event stream used by SSE.
- `workers/`: explicit work-item execution.
- `quality/`: verified provider outputs, deterministic QC evidence, and explicit human review.
- `editor/`: immutable timeline candidates, deterministic reference validation, and explicit confirmation.
- `providers/`: strict provider contracts and independent adapters; RunningHub
  visual execution and CosyVoice WAV synthesis are registered but disabled by default.

No automatic retry, route substitution, prompt rewriting, output repair, or
provider downgrade belongs in this foundation.
