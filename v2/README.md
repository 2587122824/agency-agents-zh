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

## Boundaries

- `contracts/`: API and domain contracts. Missing fields fail validation.
- `projects/`: project lifecycle rules.
- `decisions/`: append-only user decision ledger.
- `events/`: persisted event stream used by SSE.
- `workers/`: explicit work-item execution.
- `quality/`: verified provider outputs, deterministic QC evidence, and explicit human review.
- `providers/`: future provider adapters; currently intentionally empty.

No automatic retry, route substitution, prompt rewriting, output repair, or
provider downgrade belongs in this foundation.
