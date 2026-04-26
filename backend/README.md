# open_deepagents Backend

The backend is a FastAPI service that owns authentication, users, sessions,
messages, uploads, run orchestration, event streaming, generated-file export,
and the bridge into DeepAgents.

## Main Responsibilities

- Load application settings from `backend/.env`.
- Load model/provider choices from `backend/models.json`.
- Resolve the main agent package from `DEEPAGENTS_MAIN_AGENT`.
- Persist sessions, messages, uploads, runs, runtime links, and event views.
- Start and cancel DeepAgents runs.
- Normalize runtime events into the UI SSE contract.
- Export generated files from the `state` backend into downloadable uploads.
- Serve authenticated upload download links.

## Directory Map

```text
backend/
├── agents/                    Default recursive agent package
├── app/
│   ├── api/                   FastAPI routes and dependencies
│   ├── core/                  Settings, runtime catalog, database bootstrap
│   ├── db/                    SQLAlchemy models and schema management
│   ├── schemas/               API response/request schemas
│   ├── services/              Run/session orchestration
│   └── storage.py             Local upload storage
├── deepagents_integration/    DeepAgents adapter layer
├── models.example.json        Model catalog template
├── pyproject.toml             Python project and tooling
└── tests/                     Backend test suite
```

## Local Development

```bash
cd backend
uv sync --group dev
cp .env.example .env
cp models.example.json models.json
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
```

The service is available at:

- API root: `http://127.0.0.1:8000/api`
- Health check: `http://127.0.0.1:8000/health`

The app initializes the schema on startup. The explicit `init` command is useful
for MySQL and harmless for SQLite.

## Configuration

The backend reads `backend/.env`.

### Application and Auth

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL. SQLite and MySQL are supported by the included dependencies. |
| `ADMIN_AUTH_ENABLED` | Enables the login gate and per-user session isolation. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Default login credentials. |
| `ADMIN_USERS` | Optional JSON map or comma-separated `username=password` pairs. |
| `ADMIN_TOKEN_SECRET` | JWT signing secret. Use a long random value outside local dev. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins. |

### Uploads

| Setting | Purpose |
| --- | --- |
| `UPLOAD_STORAGE_DIR` | Directory for user uploads and exported generated files. |
| `MAX_UPLOAD_SIZE_BYTES` | Per-file upload limit. |

Uploads are stored on disk and tracked in the database. Browser download links
use bearer auth or an `access_token` query parameter.

### DeepAgents Runtime

| Setting | Purpose |
| --- | --- |
| `DEEPAGENTS_MAIN_AGENT` | Import spec for the main agent. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_AGENT_NAME` | Name passed to `create_deep_agent`. |
| `DEEPAGENTS_DEBUG` | Enables DeepAgents debug behavior. |
| `DEEPAGENTS_DEFAULT_TIMEZONE` | Backend timezone for persisted timestamps and auth/session events. |
| `DEEPAGENTS_RECURSION_LIMIT` | LangGraph recursion limit for runs. |
| `DEEPAGENTS_STREAM_IDLE_TIMEOUT` | Seconds without runtime/model/tool events before a run is marked failed. Use `0` to disable. |

Model providers and model-specific options belong in `models.json`.

Every web session is mapped to an internal stable LangGraph thread id. The
backend database remains a product ledger for sessions, messages, uploads, and
replayable event views.

## Agent Package Loading

The backend loads `backend/agents:AGENT` by default. The mapping can define:

- `system_prompt`
- `model`
- `workspace`
- `tools`
- `builtin_tools`
- `disabled_builtin_tools`
- `middleware`
- `hooks`
- `skills`
- `memory`
- `permissions`
- `subagents`

The recommended style is to keep the default example small: `id`,
`system_prompt`, `tools`, built-in tool visibility, hooks, skills, memory, and
explicit `subagents`. Add `model` or `permissions` only when they are needed.

Subagents are resolved recursively and support the same runtime-facing controls.
See [agents/README.md](agents/README.md).

## Built-in Tools and Permissions

Use built-in tool filtering to decide what the model can see:

```python
"builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
```

Use permissions to decide what visible file tools may access:

```python
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
]
```

`read` covers `ls`, `read_file`, `glob`, and `grep`.
`write` covers `write_file` and `edit_file`.

When permission specs are configured, unmatched read/write paths are denied.
When no subagents are active, the backend automatically hides the built-in
`task` tool.

## Prompt Injections

Prompt injections are backend-controlled runtime instructions. The feature still
exists; the implementation now lives in `app/core/session_scope.py` as
`PromptInjectionService`.

Use it from backend code, middleware, or hooks:

```python
app.state.prompt_injections.inject_prompt(
    session_id=session_id,
    content="Use the uploaded file as the source of truth.",
    visibility="hidden",
    position="before_user",
    source="backend.rule",
)
```

The public message/run APIs intentionally reject hidden prompt injection fields.

## Sandbox Backends

| Kind | Setting | Behavior |
| --- | --- | --- |
| `state` | `DEEPAGENTS_SANDBOX_KIND=state` | Virtual in-memory file state. |
| `filesystem` | `DEEPAGENTS_SANDBOX_KIND=filesystem` | File tools operate under `DEEPAGENTS_SANDBOX_ROOT_DIR`. |
| `local_shell` | `DEEPAGENTS_SANDBOX_KIND=local_shell` | File tools plus host command execution through `execute`. |
| `custom` | `DEEPAGENTS_SANDBOX_KIND=custom` | Loads `DEEPAGENTS_SANDBOX_BACKEND_SPEC`. |

For `filesystem` and `local_shell`, the app uses virtual path semantics so `/`
inside file tools maps to the configured sandbox root. See
[../docs/sandbox.md](../docs/sandbox.md).

## Run Events

DeepAgents runtime events are normalized before they reach the UI. The backend:

- streams transient assistant deltas without persisting every token;
- persists concise event views for status, tool, skill, subagent, sandbox, final
  message, and error events;
- redacts oversized strings and base64-like payloads before SSE serialization;
- caps in-memory replay backlog for long-running sessions;
- preserves terminal status events for reconnect and cancellation flows.

## Generated Files

In `state` sandbox mode, the run input can contain virtual files under
`/uploads/...`. When the runtime returns changed or new virtual files, the
backend exports them to `UPLOAD_STORAGE_DIR`, creates upload records, and attaches
them to the final assistant message.

## Useful Commands

```bash
cd backend
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy app/core/config.py app/core/runtime_catalog.py app/services/runs.py deepagents_integration
uv run pytest ../tests/backend/test_deepagents_integration.py
```
