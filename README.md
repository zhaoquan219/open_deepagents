# open_deepagents

`open_deepagents` is a FastAPI + Vue scaffold for building DeepAgents
applications with a browser chat workspace, authenticated sessions, streamed
runtime events, a model selector, Mermaid rendering, and a customizable
recursive `backend/agents/` package.

Chinese documentation: [README_CH.md](README_CH.md)

![DeepAgents web workspace screenshot](docs/images/workspace-en.png)

## What You Get

- Admin login with optional multi-user configuration.
- Per-user sessions backed by a SQL `users` / `sessions` / `uploads` /
  `events` ledger.
- A single fetch-stream run endpoint that starts a DeepAgents run and streams UI
  events.
- Runtime timeline for status, tool, subagent, sandbox, and assistant-message
  activity.
- Markdown and Mermaid rendering in assistant messages.
- Model catalog configuration for OpenAI-compatible providers.
- Recursive agent package support for prompts, tools, middleware, skills,
  memory, native filesystem permissions, and subagents.
- Native DeepAgents/LangGraph runtime wiring for `thread_id`, checkpointer,
  store, cache, and backend selection.

The current native backend keeps the runtime surface intentionally small:
sessions, ordered events, session-owned uploads, and one fetch-stream run
endpoint. Upload metadata is passed through runtime context; if a project wants
to turn those paths into an extra model message, it does so explicitly in
agent middleware.

## How It Fits Together

```text
frontend/                 Vue 3 UI: login, sessions, chat, stream timeline
backend/                  FastAPI API: auth, sessions, event ledger, DeepAgents
backend/app/runtime/      Shared DeepAgents sandbox, permission, and SSE helpers
backend/agents/           Recursive agent package loaded by the backend
backend/models.json       Model catalog shown in the UI model selector
docs/                     User guides and screenshots
packages/contracts/       Shared UI SSE contract fixtures
tests/                    Repository-level integration tests
verification/             Scaffold and contract audit helpers
```

Run flow:

1. The frontend logs in and stores a bearer token.
2. The user selects or creates a session.
3. The frontend sends a prompt to
   `POST /api/sessions/{session_id}/runs/stream`.
4. The backend persists `run.started` and `user.message` events.
5. `backend/agents:AGENT` is resolved into DeepAgents runtime inputs.
6. DeepAgents streams runtime events through `graph.astream_events(...)`.
7. The backend stores normalized event rows and emits SSE envelopes.
8. The frontend updates the transcript from message events and the timeline from
   status/tool/runtime events.

## Quick Start

### 1. Configure the backend

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

Edit `backend/models.json` and provide credentials through environment
placeholders such as `${OPENAI_API_KEY}`.

Important `.env` settings:

| Setting | Purpose |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Default login credentials. |
| `ADMIN_USERS` | Optional JSON map or comma-separated `username=password` pairs. |
| `ADMIN_TOKEN_SECRET` | JWT signing secret. Use a long random value outside local dev. |
| `DATABASE_URL` | SQLite, PostgreSQL, or MySQL SQLAlchemy URL for the product ledger. |
| `DEEPAGENTS_MAIN_AGENT` | Main agent import spec. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_SANDBOX_PROFILE` | High-level sandbox preset: `safe`, `files`, `shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | Workspace root for `files` and `shell` sandbox profiles. |
| `DEEPAGENTS_UPLOAD_ROOT_DIR` | Storage root mounted read-only at `/uploads`. |
| `DEEPAGENTS_CHECKPOINT_BACKEND` | `sqlite` by default; supports `memory`, `sqlite`, and `postgresql`. |
| `BACKEND_LOG_LEVEL` | `info` by default; `debug` logs concise runtime event summaries. |

Recommended setup:

- Local development: copy `.env.example`, change the admin password and token
  secret, then fill `models.json`.
- Keep `DEEPAGENTS_SANDBOX_PROFILE=safe` unless you need filesystem writes or a
  trusted local shell.
- Production deployment: set `ENVIRONMENT=production`, strong auth secrets, and
  `DEEPAGENTS_CHECKPOINT_BACKEND=sqlite` or `postgresql`.

Product state and runtime state are deliberately separate. `DATABASE_URL`
stores the product tables (`users`, `sessions`, `runs`, `uploads`, `events`);
sqlite checkpoint state defaults to `./data/checkpoints.db`, while postgresql
checkpoint state uses `DEEPAGENTS_CHECKPOINT_DATABASE_URL`.

Product ledger examples:

```dotenv
DATABASE_URL=sqlite+pysqlite:///./data/backend.db
DATABASE_URL=postgresql+psycopg://app:change-me@127.0.0.1:5432/open_deepagents
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

The backend creates the database if needed, then initializes the scaffold
tables. `DATABASE_URL` stores users, sessions, uploads, runs, and events; LangGraph
checkpoint/store persistence is configured separately via the checkpoint
settings above.

### 2. Start the backend

```bash
cd backend
uv sync --group dev
uv run python -m app
```

`python -m app` is the recommended entry point. It sets the asyncio event loop
policy before uvicorn binds its socket, which is required for the Postgres
checkpoint/store backend on Windows (psycopg async mode cannot run on the default
`ProactorEventLoop`). It respects `BACKEND_HOST`, `BACKEND_PORT`, and
`BACKEND_RELOAD` (reload defaults to on). The bare
`uv run uvicorn app.main:app --reload` command works for SQLite/in-memory
checkpoints, but on Windows with Postgres checkpoints it will hang on every
request, so prefer `python -m app`.

The app initializes the schema on startup. The API defaults to:

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server defaults to `http://127.0.0.1:5173`.

## API Contract

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | Login and receive a bearer token. |
| `GET /api/auth/me` | Return the current user. |
| `GET /api/models` | Return safe model selector metadata. |
| `GET /api/sessions` | List the current user's sessions. |
| `POST /api/sessions` | Create a session. |
| `PATCH /api/sessions/{session_id}` | Update title or metadata. |
| `DELETE /api/sessions/{session_id}` | Archive an owned session, hide normal history, and revoke uploads. |
| `GET /api/sessions/{session_id}/events?after_seq=N` | Load durable ordered history. |
| `POST /api/sessions/{session_id}/uploads` | Store a session-owned upload row and return `/uploads/{session_id}/{filename}`. |
| `POST /api/sessions/{session_id}/runs/stream` | Start and stream one run. |

The client stops a run through `POST /api/runs/{run_id}/cancel`, then closes the
active fetch stream.

## Model Catalog

`backend/models.json` controls the model selector shown in the UI.

```json
{
  "model": "openai/gpt-5-4",
  "provider": {
    "openai": {
      "name": "OpenAI",
      "options": {
        "api_key": "${OPENAI_API_KEY}",
        "base_url": "https://api.openai.com/v1"
      },
      "models": {
        "gpt-5-4": {
          "name": "GPT-5.4",
          "model": "gpt-5.4"
        }
      }
    }
  }
}
```

Provider `options` and model fields are passed to `ChatOpenAI`. Keep secrets in
environment variables; public model metadata omits secret values.

## Agent Package

The default main agent is `backend/agents:AGENT`.

```python
AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "permissions": [
        {
            "operations": ("read",),
            "paths": ["/workspace/main", "/skills", "/uploads"],
        },
        {"operations": ("write",), "paths": ["/workspace/main/output"]},
    ],
    "subagents": SUBAGENTS,
}
```

Use native `permissions[].operations` to control filesystem access. Built-in
tool visibility is no longer configured through the agent mapping; file tools
are allowed or denied by operation and virtual path.
Package-local `tools`, `middleware`, `skills`, `memory`, and `subagents` resolve
relative to the current agent package. Selectors can be explicit lists or `"*"`
to discover all component exports in that package folder.

See [backend/agents/README.md](backend/agents/README.md) for package examples.

## Sandbox Backends

| Kind | Best for | Notes |
| --- | --- | --- |
| `safe` | Default virtual file state plus read-only `/skills` and `/uploads`. | No host shell. |
| `files` | File tools over a controlled data directory plus read-only mounts. | Use only when file writes are needed. |
| `shell` | Trusted local command execution. | Runs commands on the host. |
| `custom` | Bring your own backend. | Advanced override. |

Read [docs/sandbox.md](docs/sandbox.md) before enabling filesystem or shell
access for untrusted users.

## Verification

Backend:

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest
```

Frontend:

```bash
cd frontend
npm run check
```

Repository-level checks:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest tests tests/backend
python verification/scaffold_audit.py
```
