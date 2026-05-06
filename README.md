# open_deepagents

`open_deepagents` is a FastAPI + Vue scaffold for building DeepAgents
applications with a browser chat workspace, authenticated sessions, streamed
runtime events, a model selector, Mermaid rendering, and a customizable
recursive `backend/agents/` package.

Chinese documentation: [README_CH.md](README_CH.md)

![DeepAgents web workspace screenshot](docs/images/workspace-en.png)

## What You Get

- Admin login with optional multi-user configuration.
- Per-user sessions backed by a SQL `users` / `sessions` / `events` ledger.
- A single fetch-stream run endpoint that starts a DeepAgents run and streams UI
  events.
- Runtime timeline for status, tool, subagent, sandbox, and assistant-message
  activity.
- Markdown and Mermaid rendering in assistant messages.
- Model catalog configuration for OpenAI-compatible providers.
- Recursive agent package support for prompts, tools, middleware, skills,
  memory, permissions, built-in tool visibility, and subagents.
- Native DeepAgents/LangGraph runtime wiring for `thread_id`, checkpointer,
  store, cache, and backend selection.

The current native backend keeps the runtime surface intentionally small:
sessions, ordered events, and one fetch-stream run endpoint. Upload/download,
generated-file export, split run management, and prompt-injection services are
not part of this scaffold. The frontend still displays attachment-shaped
message metadata when present in history, but file upload is currently a local
pending-attachment placeholder.

## How It Fits Together

```text
frontend/                 Vue 3 UI: login, sessions, chat, stream timeline
backend/                  FastAPI API: auth, sessions, event ledger, DeepAgents
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
| `DATABASE_URL` | SQLite or MySQL SQLAlchemy URL for the product ledger. |
| `DEEPAGENTS_MAIN_AGENT` | Main agent import spec. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_RUNTIME_DRIVER` | Runtime mode: `memory`, `sqlite`, `postgres`, or `factory`. |
| `DEEPAGENTS_RUNTIME_DATABASE_URL` | Required for official `sqlite` or `postgres` runtime modes. Must not target `DATABASE_URL`. |
| `DEEPAGENTS_RUNTIME_FACTORY` | Optional advanced durable runtime factory/object import spec. |
| `DEEPAGENTS_RUNTIME_PERSISTENCE` | Backward-compatible low-level runtime mode. |
| `DEEPAGENTS_RUNTIME_SPEC` | Backward-compatible low-level runtime bundle hook. |
| `DEEPAGENTS_SANDBOX_PROFILE` | High-level sandbox preset: `safe`, `files`, `shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_KIND` | `state`, `filesystem`, `local_shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_MAX_OUTPUT_BYTES` | Max captured shell output for `local_shell`. |
| `DEEPAGENTS_SANDBOX_INHERIT_ENV` | Whether `local_shell` inherits host environment variables. |
| `DEEPAGENTS_SANDBOX_ENV` | Optional JSON object of extra environment variables for `local_shell`. |
| `DEEPAGENTS_BACKEND_SPEC` | Import spec for a custom DeepAgents backend. |

Recommended setup:

- Local development: set `DEEPAGENTS_RUNTIME_DRIVER=memory` and `DEEPAGENTS_SANDBOX_PROFILE=safe`.
- Standard durable deployment: set `ENVIRONMENT=production`,
  `DEEPAGENTS_RUNTIME_DRIVER=postgres`, and a separate
  `DEEPAGENTS_RUNTIME_DATABASE_URL` for LangGraph runtime persistence.
- Advanced override: set `DEEPAGENTS_RUNTIME_FACTORY` only if you want to
  provide your own durable checkpointer/store/cache bundle.
- Legacy `DEEPAGENTS_RUNTIME_PERSISTENCE`, `DEEPAGENTS_RUNTIME_SPEC`, and the low-level `*_SPEC` settings still work as advanced compatibility escape hatches.

Product state and runtime state are deliberately separate. `DATABASE_URL`
stores exactly the four product tables (`users`, `sessions`, `runs`, `events`);
official sqlite/postgres runtime modes require a distinct
`DEEPAGENTS_RUNTIME_DATABASE_URL` for LangGraph checkpoint/store tables.

MySQL example for the product ledger:

```dotenv
DATABASE_URL=mysql+pymysql://app:change-me@127.0.0.1:3306/open_deepagents?charset=utf8mb4
```

The backend creates the database if needed, then initializes the scaffold
tables. This MySQL connection stores users, sessions, runs, and events;
LangGraph runtime persistence is configured separately via the runtime settings
above.

### 2. Start the backend

```bash
cd backend
uv sync --group dev
uv run uvicorn app.main:app --reload
```

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
| `DELETE /api/sessions/{session_id}` | Delete an owned session and events. |
| `GET /api/sessions/{session_id}/events?after_seq=N` | Load durable ordered history. |
| `POST /api/sessions/{session_id}/runs/stream` | Start and stream one run. |

There is no split `/api/runs` endpoint. The client cancels by aborting the fetch
stream.

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
    "builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

Use `permissions` when you expose file tools. `workspace` is metadata only; it is
not a security boundary.
Package-local `skills`, `memory`, and `subagents` resolve relative to the
current agent package, so selectors like `SKILLS = ["skill-creator"]` work out
of the box.

See [backend/agents/README.md](backend/agents/README.md) for package examples.

## Sandbox Backends

| Kind | Best for | Notes |
| --- | --- | --- |
| `state` | Default virtual file state. | No host shell. |
| `files` | File tools over a controlled directory. | Set `DEEPAGENTS_SANDBOX_ROOT_DIR`. |
| `local_shell` | Trusted local command execution. | Runs commands on the host. |
| `custom` | Bring your own backend. | Set `DEEPAGENTS_BACKEND_SPEC`. |

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
