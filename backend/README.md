# Backend

FastAPI backend for the open_deepagents workspace. This lane owns auth,
session/message/upload persistence, run orchestration, schema initialization,
and the bridge into DeepAgents.

## Current Architecture

The runtime is configured through an agent package plus a model catalog:

- Main agent: `backend/agents:AGENT`
- Main prompt: `backend/agents/prompts/system.md`
- Tools: `backend/agents/tools`
- Middleware: `backend/agents/middleware`
- Run/upload hooks: `backend/agents/hooks`
- Skills: `backend/agents/skills`
- Memory: `backend/agents/memory`
- Subagents: `backend/agents/subagents`
- Models: `backend/models.json`

The previous extension-directory and environment-only model configuration have
been retired. Keep runtime customization in `backend/agents/` and model/provider
selection in `backend/models.json`.

## Local Development

```bash
cd backend
uv sync --group dev
cp .env.example .env
cp models.example.json models.json
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
```

The app also initializes the schema on startup. The explicit `init` command is
useful for MySQL deployments and harmless for SQLite.

## Environment

The backend reads `backend/.env`.

Core runtime settings:

| Variable | Purpose |
| --- | --- |
| `DEEPAGENTS_MAIN_AGENT` | Import spec for the main agent. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_DEFAULT_MODEL` | Catalog model ID, for example `openai/gpt-5-4`. |
| `DEEPAGENTS_AGENT_NAME` | Runtime graph name. |
| `DEEPAGENTS_BUILTIN_TOOLS` | Optional built-in tool allowlist. |
| `DEEPAGENTS_DISABLED_BUILTIN_TOOLS` | Optional built-in tool blocklist. |
| `DEEPAGENTS_SANDBOX_KIND` | `state`, `filesystem`, `local_shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_BACKEND_SPEC` | Import spec for custom backend factories. |

Auth/session settings:

- `ADMIN_AUTH_ENABLED=true` isolates sessions by authenticated username.
- `ADMIN_AUTH_ENABLED=false` disables the login gate for trusted local use.
- `ADMIN_USERS` can add multiple users as JSON, for example
  `{"alice":"alice-secret","bob":"bob-secret"}`.

Uploads:

- `UPLOAD_STORAGE_DIR=./data/uploads` resolves under `backend/data/uploads`.
- User uploads are attached to user messages.
- Generated files from the `state` backend are exported as uploads and attached
  to the final assistant message.
- `/api/uploads/{upload_id}/content` supports bearer auth and `access_token`
  query auth so browser download links can work.

## Agent Hooks

Run-input hooks receive:

- `session_id`
- `run_id`
- `role`
- `content`
- `attachments`
- `is_current_run`

Return a string or `{"content": "..."}` to replace message content, or `None`
to leave it unchanged.

Upload hooks receive metadata only and return a mapping to merge into
`UploadRecord.extra`.

Default examples live in `backend/agents/hooks/attachment_hooks.py`.

## Built-in Tool Filtering

DeepAgents built-ins can be filtered without editing source:

```dotenv
DEEPAGENTS_BUILTIN_TOOLS=write_todos,ls,read_file,glob,grep,task
DEEPAGENTS_DISABLED_BUILTIN_TOOLS=execute,write_file,edit_file
```

The allowlist is applied first. The blocklist is then applied to the remaining
built-ins. Custom tools from `backend/agents/tools` pass through unchanged.

## Verification

From the repository root:

```bash
PYTHONPATH=.:backend backend/.venv/bin/pytest -q
backend/.venv/bin/ruff check backend tests
backend/.venv/bin/mypy backend/app backend/deepagents_integration
```
