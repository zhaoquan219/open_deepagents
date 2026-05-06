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

The native backend intentionally does not include upload/download storage or
generated-file export. Those can be built as extensions on top of the event
projection and native DeepAgents runtime.

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
| `DEEPAGENTS_AGENT_NAME` | Name passed to `create_deep_agent`. |
| `DEEPAGENTS_RECURSION_LIMIT` | LangGraph recursion limit for runs. |
| `DEEPAGENTS_RUNTIME_DRIVER` | Runtime mode: `memory`, `sqlite`, `postgres`, or `factory`. |
| `DEEPAGENTS_RUNTIME_DATABASE_URL` | Required for official `sqlite` or `postgres` runtime modes. Must differ from `DATABASE_URL`. |
| `DEEPAGENTS_RUNTIME_FACTORY` | Optional `module:attribute` hook returning checkpointer, store, and optional cache. |
| `DEEPAGENTS_RUNTIME_PERSISTENCE` | Backward-compatible runtime mode alias. |
| `DEEPAGENTS_RUNTIME_SPEC` | Optional `module:attribute` factory/object that exposes `checkpointer`, `store`, and optional `cache`. |
| `DEEPAGENTS_SANDBOX_PROFILE` | High-level sandbox preset: `safe`, `files`, `shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_KIND` | `state`, `filesystem`, `local_shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_MAX_OUTPUT_BYTES` | Max captured shell output for `local_shell`. |
| `DEEPAGENTS_SANDBOX_INHERIT_ENV` | Whether `local_shell` inherits host environment variables. |
| `DEEPAGENTS_SANDBOX_ENV` | Optional JSON object of extra environment variables for `local_shell`. |
| `DEEPAGENTS_BACKEND_SPEC` | Optional custom DeepAgents backend import spec. |
| `AUDIT_COMPILED_PROMPTS` | `hash` by default; `redacted`, `full`, and `off` are explicit modes. |

Recommended flow:

- Local development: set `DEEPAGENTS_RUNTIME_DRIVER=memory` and `DEEPAGENTS_SANDBOX_PROFILE=safe`.
- Production deployment: set `ENVIRONMENT=production`, strong auth secrets, and
  `DEEPAGENTS_RUNTIME_DRIVER=postgres` with official LangGraph persistence
  packages plus a separate `DEEPAGENTS_RUNTIME_DATABASE_URL`, or provide
  `DEEPAGENTS_RUNTIME_FACTORY`.
- Advanced override: set `DEEPAGENTS_RUNTIME_FACTORY` when a custom durable
  runtime bundle should replace the built-in resolver.
- Legacy `DEEPAGENTS_RUNTIME_PERSISTENCE`, `DEEPAGENTS_RUNTIME_SPEC`, and the low-level `*_SPEC` settings remain as advanced compatibility escape hatches.

Product tables never store checkpoint internals. Runtime state is restored by
the LangGraph checkpointer/store through `sessions.thread_id`.
The official sqlite/postgres runtime modes require their own runtime DSN and
refuse to share `DATABASE_URL`; `/ready` validates that the product database
contains exactly the four product tables.

### MySQL Example

```dotenv
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

The backend creates the target MySQL database on startup when needed, then
initializes the scaffold tables.

Important distinction:

- `DATABASE_URL` stores users, sessions, runs, and ordered events.
- `DEEPAGENTS_RUNTIME_DRIVER` and `DEEPAGENTS_RUNTIME_FACTORY` configure
  LangGraph/DeepAgents runtime state.
- `DEEPAGENTS_RUNTIME_PERSISTENCE` and `DEEPAGENTS_RUNTIME_SPEC` remain
  backward-compatible low-level aliases.

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
| `POST /api/sessions/{session_id}/runs` | Run to completion and return the final run row. |
| `POST /api/sessions/{session_id}/runs/stream` | Start one run and stream SSE events. |
| `GET /api/runs/{run_id}` | Return run status and audit metadata. |
| `POST /api/runs/{run_id}/cancel` | Mark a running run cancelled. |
| `GET /api/runs/{run_id}/events` | Load durable ordered events for one run. |
| `GET /health` | Process liveness. |
| `GET /ready` | DB, schema, runtime, catalog, sandbox, and agent readiness. |

Each run appends `run.started`, `user.message`, audit events, runtime events,
and a terminal `run.completed`, `run.failed`, or `run.cancelled` event. Events
are persisted before they are emitted to stream clients.

## Agent Package Loading

The backend loads `backend/agents:AGENT` by default. The mapping can define:

- `system_prompt`
- `tools`
- `middleware`
- `skills`
- `memory`
- `permissions`
- `subagents`
- `builtin_tools`
- `disabled_builtin_tools`

`builtin_tools` and `disabled_builtin_tools` are converted into a small
middleware that filters DeepAgents built-in tools before model calls.
`permissions` are passed through to DeepAgents for file-tool authorization.
Package-local `skills`, `memory`, and `subagents` are resolved relative to the
current agent package instead of the backend root.

See [agents/README.md](agents/README.md) for examples.

## Native Integration Helpers

The current FastAPI app owns application policy and request handling. `app/`
decides auth, sessions, SQL persistence, runtime configuration, and agent
resolution. `app/runtime/` now holds the raw DeepAgents-facing helpers:

- import-spec loading;
- built-in tool filtering middleware;
- permissions and sandbox backend resolution;
- run-context schema utilities;
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
