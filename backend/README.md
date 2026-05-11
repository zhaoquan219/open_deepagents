# open_deepagents Backend

The backend is a compact FastAPI service around DeepAgents. It owns auth,
per-user sessions, durable run rows, a typed SQL event projection, model/agent
configuration, and fetch-stream execution. DeepAgents and LangGraph own runtime
state via the configured `thread_id`, checkpointer, store, cache, and backend.

## Current Scope

- Login with JWT bearer tokens.
- Sync configured admin users into SQL on startup.
- Persist exactly four product tables: `users`, `sessions`, `runs`, and `events`.
- Resolve `backend/agents:AGENT`, `backend/models.json`, tools, middleware,
  skills, memory, permissions, subagents, and built-in tool visibility.
- Stream DeepAgents runtime events after persisting each durable event.
- Support SQLite, PostgreSQL, and MySQL for the product database.
- Resolve LangGraph runtime persistence through memory, official SQLite/Postgres
  packages, or an explicit `DEEPAGENTS_RUNTIME_FACTORY`.
- Store session uploads and expose model-facing `/uploads/...` virtual paths.

## Directory Map

```text
backend/
├── agents/                    Default recursive agent package
├── app/
│   ├── main.py                FastAPI app factory and startup
│   ├── routes.py              Auth, models, sessions, history, run stream
│   ├── settings.py            .env settings and import-spec resolution
│   ├── db.py                  SQLAlchemy users/sessions/runs/events schema
│   ├── auth.py                Password hashing, JWT, current user
│   ├── catalog.py             models.json and agent package resolver
│   ├── agent.py               create_deep_agent wiring
│   └── runtime/               DeepAgents runtime helpers owned by the app
├── models.example.json        Model catalog template
├── pyproject.toml             Python project and tooling
└── tests/                     Backend tests
```

## Local Development

```bash
cd backend
uv sync --group dev
cp .env.example .env
cp models.example.json models.json
uv run uvicorn app.main:app --reload
```

The schema is created on application startup. The service defaults to:

- API: `http://127.0.0.1:8000/api`
- Health: `http://127.0.0.1:8000/health`

## Configuration

The backend reads `backend/.env`.

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. SQLite, PostgreSQL, and MySQL are product DB targets. |
| `ADMIN_AUTH_ENABLED` | Enables login and per-user session isolation. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Default configured login user. |
| `ADMIN_USERS` | Optional JSON map or comma-separated `username=password` pairs. |
| `ADMIN_TOKEN_SECRET` | JWT signing secret. Use a long random value outside local dev. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins. |
| `DEEPAGENTS_MAIN_AGENT` | Main agent import spec. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_SANDBOX_PROFILE` | High-level sandbox preset: `safe`, `files`, `shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | Workspace root used by `files` and `shell` profiles. |
| `DEEPAGENTS_UPLOAD_ROOT_DIR` | Session upload root mounted read-only at `/uploads`. |
| `DEEPAGENTS_CHECKPOINT_BACKEND` | `sqlite` by default; supports `memory`, `sqlite`, and `postgresql`. |
| `DEEPAGENTS_CHECKPOINT_DATABASE_URL` | Optional sqlite/postgresql checkpoint/store DSN. |
| `BACKEND_LOG_LEVEL` | `info` by default; `debug` logs every raw agent update event. |
| `AUDIT_COMPILED_PROMPTS` | `hash` by default; `redacted`, `full`, and `off` are explicit modes. |

Recommended flow:

- Local development: copy `.env.example`, change the admin password and token
  secret, then fill `models.json`.
- Keep `DEEPAGENTS_SANDBOX_PROFILE=safe` unless you need filesystem writes or a
  trusted local shell.
- Production deployment: set `ENVIRONMENT=production`, strong auth secrets, and
  `DEEPAGENTS_CHECKPOINT_BACKEND=sqlite` or `postgresql`.

Product tables never store checkpoint internals. Runtime state is restored by
the LangGraph checkpointer/store through `sessions.thread_id`.
The sqlite checkpoint mode defaults to `./data/checkpoints.db`. Postgresql
requires `DEEPAGENTS_CHECKPOINT_DATABASE_URL`. Checkpoint stores refuse to share
`DATABASE_URL`; `/ready` validates that the product database contains exactly
the four product tables.

### Product Database Examples

```dotenv
DATABASE_URL=sqlite+pysqlite:///./data/backend.db
DATABASE_URL=postgresql+psycopg://app:change-me@127.0.0.1:5432/open_deepagents
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

The backend creates the target database on startup when needed, then initializes
the scaffold tables.

Important distinction:

- `DATABASE_URL` stores users, sessions, runs, and ordered events.
- `DEEPAGENTS_CHECKPOINT_BACKEND` and `DEEPAGENTS_CHECKPOINT_DATABASE_URL`
  configure LangGraph/DeepAgents checkpoint and store state.

## API Contract

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | Login and receive a bearer token. |
| `GET /api/auth/me` | Return the current user. |
| `GET /api/models` | Return safe model selector metadata. |
| `GET /api/sessions` | List the current user's sessions. |
| `POST /api/sessions` | Create a session with a stable LangGraph `thread_id`. |
| `PATCH /api/sessions/{session_id}` | Update session `title` or `metadata`. |
| `DELETE /api/sessions/{session_id}` | Delete an owned session and its events. |
| `GET /api/sessions/{session_id}/events?after_seq=N` | Load durable ordered history. |
| `POST /api/sessions/{session_id}/uploads` | Store one owned upload and return its model-facing `/uploads/...` path. |
| `POST /api/sessions/{session_id}/runs` | Run to completion and return the final run row. |
| `POST /api/sessions/{session_id}/runs/stream` | Start one run and stream SSE events. |
| `GET /api/uploads/{upload_id}/content` | Download an owned upload. |
| `DELETE /api/uploads/{upload_id}` | Delete an owned upload. |
| `GET /api/runs/{run_id}` | Return run status and audit metadata. |
| `POST /api/runs/{run_id}/cancel` | Mark a running run cancelled. |
| `GET /api/runs/{run_id}/events` | Load durable ordered events for one run. |
| `GET /health` | Process liveness. |
| `GET /ready` | DB, schema, runtime, catalog, sandbox, and agent readiness. |

Each run appends `run.started`, `user.message`, any middleware-generated
`system.message` rows, audit events, runtime events, and a terminal
`run.completed`, `run.failed`, or `run.cancelled` event. Events are persisted
before they are emitted to stream clients.

## Agent Package Loading

The backend loads `backend/agents:AGENT` by default. The mapping can define:

- `system_prompt`
- `tools`
- `middleware`
- `skills`
- `memory`
- `permissions`
- `subagents`

`permissions[].builtin_tools` filters DeepAgents built-in tools before model
calls. File built-ins use the same permission entries' `paths` for file-tool
authorization; `"*"` allows every known built-in.
Package-local `tools`, `middleware`, `skills`, `memory`, and `subagents` are
resolved relative to the current agent package instead of the backend root.
Use `"*"` to discover all exports in a component folder.

See [agents/README.md](agents/README.md) for examples.

## Native Integration Helpers

The current FastAPI app owns application policy and request handling. `app/`
decides auth, sessions, SQL persistence, runtime configuration, and agent
resolution. `app/runtime/` now holds the raw DeepAgents-facing helpers:

- import-spec loading;
- built-in tool filtering middleware;
- permissions and sandbox backend resolution;
- skill-source routing;
- SSE normalization helpers used by backend runtime tests.

Keep new runtime behavior in these shared helpers instead of rebuilding local
normalizers inside route handlers.

## Verification

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest

cd ..
PYTHONPATH=backend backend/.venv/bin/python -m pytest tests tests/backend
python verification/scaffold_audit.py
```
