# open_deepagents Backend

The backend is a compact FastAPI service around DeepAgents. It owns auth,
per-user sessions, durable run rows, a typed SQL event projection, model/agent
configuration, and fetch-stream execution. DeepAgents and LangGraph own runtime
state via the configured `thread_id`, checkpointer, store, cache, and backend.

## Current Scope

- Login with JWT bearer tokens.
- Sync configured admin users into SQL on startup.
- Persist product tables: `users`, `sessions`, `runs`, `uploads`, and `events`.
- Resolve `backend/agents:AGENT`, `backend/models.json`, tools, middleware,
  skills, memory, native filesystem permissions, and subagents.
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
│   ├── db.py                  SQLAlchemy users/sessions/uploads/runs/events schema
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
uv run python -m app
```

`python -m app` is the recommended entry point: it configures the asyncio event
loop policy before uvicorn binds its socket, which is required for the Postgres
checkpoint/store backend on Windows (psycopg's async mode cannot run on Windows'
default `ProactorEventLoop`). It honors `BACKEND_HOST`, `BACKEND_PORT`, and
`BACKEND_RELOAD` (reload is on by default).

The bare `uv run uvicorn app.main:app --reload` command still works for SQLite/
in-memory checkpoints, but on Windows with the Postgres checkpoint backend it
binds the listening socket before the loop policy can be set and the server will
accept connections without ever responding. Use `python -m app` instead.

The schema is created on application startup. The service defaults to:

- API: `http://127.0.0.1:8000/api`
- Health: `http://127.0.0.1:8000/health`

## Concurrency and scaling

Agent runs execute on a dedicated asyncio loop (`app/runtime_loop.py`) that owns
the LangGraph checkpointer/store. Because model calls and checkpoint I/O are
async, many runs interleave on this single loop concurrently — a run that is
waiting on the model never blocks other runs. Per-event product-database writes
are dispatched to a thread pool so they never block the loop either.

For CPU-bound throughput beyond one core, scale out with multiple worker
processes — each gets its own runtime loop, checkpointer, and database
connections (so LangGraph's per-saver write lock no longer serializes across
workers):

```bash
# production: disable reload and run N worker processes
BACKEND_RELOAD=false BACKEND_WORKERS=4 uv run python -m app
```

`BACKEND_WORKERS` is ignored while `BACKEND_RELOAD` is on (uvicorn does not allow
reload together with multiple workers). Behind a load balancer you can also run
multiple instances/containers for horizontal scaling.

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
| `BACKEND_LOG_LEVEL` | `info` by default; `debug` logs concise runtime event summaries. |
| `AUDIT_COMPILED_PROMPTS` | `hash` by default; `redacted`, `full`, and `off` are explicit modes. |

Recommended flow:

- Local development: copy `.env.example`, change the admin password and token
  secret, then fill `models.json`.
- Keep `DEEPAGENTS_SANDBOX_PROFILE=safe` unless you need filesystem writes or a
  trusted local shell.
- Production deployment: set `ENVIRONMENT=production`, strong auth secrets, and
  `DEEPAGENTS_CHECKPOINT_BACKEND=sqlite` or `postgresql`.

Product tables never store checkpoint internals. Runtime state is restored by
the LangGraph checkpointer/store keyed on `sessions.id` (the session id is used
directly as the LangGraph `thread_id`).
The sqlite checkpoint mode defaults to `./data/checkpoints.db`. Postgresql
requires `DEEPAGENTS_CHECKPOINT_DATABASE_URL`. Checkpoint stores refuse to share
`DATABASE_URL`; `/ready` validates the product schema without repairing it and
performs a read-only runtime checkpoint/store probe.

### Product Database Examples

```dotenv
DATABASE_URL=sqlite+pysqlite:///./data/backend.db
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/open_deepagents
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

The backend auto-provisions storage on startup for every supported engine:

- The target database is created automatically when it does not exist (PostgreSQL
  and MySQL), and the SQLite file's parent directory is created for SQLite.
- The scaffold tables (`users`, `sessions`, `runs`, `uploads`, `events`) are
  created if missing (idempotent), so a missing table is never a hard error.
- The same applies to the LangGraph checkpoint/store database when
  `DEEPAGENTS_CHECKPOINT_BACKEND=postgresql`.

Driver normalization keeps URLs forgiving:

- A bare `postgresql://` (or `postgres://`) is rewritten to the installed psycopg
  v3 driver (`postgresql+psycopg://`); you do not need psycopg2.
- A bare `mysql://` is rewritten to `mysql+pymysql://`.
- `DEEPAGENTS_CHECKPOINT_DATABASE_URL` accepts both `postgresql://` and
  `postgresql+psycopg://`; the driver suffix is stripped before it is handed to
  LangGraph/psycopg.

MySQL notes:

- MySQL is a product-DB target only. LangGraph's official checkpoint/store
  packages cover SQLite and PostgreSQL, so with a MySQL `DATABASE_URL` use a
  `sqlite`/`postgresql`/`memory` checkpoint backend (or a custom
  `DEEPAGENTS_RUNTIME_FACTORY`).
- The "one running run per session" guarantee uses a partial unique index on
  SQLite/PostgreSQL. MySQL has no partial indexes, so that index is skipped and
  the rule is enforced by the application-level active-run check.

Cross-platform: all of the above works on both Windows and Linux/macOS. On
Windows, the Postgres checkpoint backend automatically switches to a selector
event loop, because psycopg's async mode cannot run on the default
`ProactorEventLoop`; this is a no-op elsewhere.

Important distinction:

- `DATABASE_URL` stores users, sessions, uploads, runs, and ordered events.
- `DEEPAGENTS_CHECKPOINT_BACKEND` and `DEEPAGENTS_CHECKPOINT_DATABASE_URL`
  configure LangGraph/DeepAgents checkpoint and store state.

### Local PostgreSQL setup

The product DB and checkpoint DB only need a running server and valid
credentials; the databases themselves are created for you.

- macOS: `brew install postgresql@16 && brew services start postgresql@16`
- Debian/Ubuntu: `sudo apt-get install -y postgresql && sudo service postgresql start`
- Windows (Chocolatey): `choco install postgresql16 --params "/Password:postgres" -y`
  installs the server as the `postgresql-x64-16` service and adds `psql` to `PATH`.
- Docker (any OS):
  `docker run --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16`

Then point `.env` at it, for example:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/open_deepagents
DEEPAGENTS_CHECKPOINT_BACKEND=postgresql
DEEPAGENTS_CHECKPOINT_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/open_deepagents_checkpoints
```

### Local MySQL setup

- macOS: `brew install mysql && brew services start mysql`
- Debian/Ubuntu: `sudo apt-get install -y mysql-server && sudo service mysql start`
- Windows (Chocolatey): `choco install mysql -y` installs the `mysql` service
  (root has an empty password by default) and adds `mysql` to `PATH`.
- Docker (any OS):
  `docker run --name mysql -e MYSQL_ROOT_PASSWORD=change-me -p 3306:3306 -d mysql:8`

Then, with a SQLite checkpoint (MySQL is product-DB only):

```dotenv
DATABASE_URL=mysql+pymysql://root:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
DEEPAGENTS_CHECKPOINT_BACKEND=sqlite
```

## API Contract

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | Login and receive a bearer token. |
| `GET /api/auth/me` | Return the current user. |
| `GET /api/models` | Return safe model selector metadata. |
| `GET /api/sessions` | List the current user's sessions. |
| `POST /api/sessions` | Create a session whose id doubles as the LangGraph `thread_id`. |
| `PATCH /api/sessions/{session_id}` | Update session `title` or `metadata`. |
| `DELETE /api/sessions/{session_id}` | Archive an owned session, hide normal history, and revoke its uploads. |
| `GET /api/sessions/{session_id}/events?after_seq=N` | Load durable ordered history. |
| `POST /api/sessions/{session_id}/uploads` | Store one owned upload and return its model-facing `/uploads/...` path. |
| `POST /api/sessions/{session_id}/runs` | Run to completion and return the final run row. |
| `POST /api/sessions/{session_id}/runs/stream` | Start one run and stream SSE events. |
| `GET /api/uploads/{upload_id}/content` | Download an owned upload with bearer-header auth. |
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

`permissions[].operations` is the native DeepAgents filesystem permission
surface. It controls read/write authorization for virtual sandbox paths. Built-in
tool visibility is not configured by the scaffold.
Package-local `tools`, `middleware`, `skills`, `memory`, and `subagents` are
resolved relative to the current agent package instead of the backend root.
Use `"*"` to discover all exports in a component folder.

See [agents/README.md](agents/README.md) for examples.

## Native Integration Helpers

The current FastAPI app owns application policy and request handling. `app/`
decides auth, sessions, SQL persistence, runtime configuration, and agent
resolution. `app/runtime/` now holds the raw DeepAgents-facing helpers:

- import-spec loading;
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
