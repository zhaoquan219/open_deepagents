# open_deepagents

`open_deepagents` is a ready-to-run web console for building DeepAgents
applications. It includes a FastAPI backend, a Vue chat workspace, persistent
sessions, uploads and generated-file downloads, streamed runtime events, model
selection, Mermaid rendering, sandbox controls, and a recursive agent package you
can customize without changing the app shell.

Chinese documentation: [README_CH.md](README_CH.md)

![DeepAgents web workspace screenshot](docs/images/workspace-en.png)

## What You Get

- Admin login with optional multi-user configuration.
- Session history with stored user and assistant messages.
- File upload support with attachment context passed into agent runs.
- Streaming chat responses over SSE.
- Runtime timeline for run, tool, skill, sandbox, and subagent activity.
- Markdown and Mermaid rendering in assistant messages.
- PNG copy support for rendered Mermaid diagrams when the browser supports image
  clipboard writes.
- Model catalog configuration for OpenAI-compatible providers.
- A recursive `backend/agents/` package for prompts, tools, middleware, hooks,
  skills, memory, and subagents.
- Sandbox backends for virtual state, filesystem access, local shell, or custom
  DeepAgents backends.

## How It Fits Together

```text
frontend/                 Vue 3 UI: login, sessions, chat, uploads, timeline
backend/                  FastAPI API: auth, persistence, uploads, run orchestration
backend/agents/           Recursive agent package loaded by the backend
backend/models.json       Model catalog shown in the UI model selector
docs/                     User guides and screenshots
packages/contracts/       Shared event contract fixtures
tests/                    Repository-level backend integration tests
verification/             Scaffold and contract audit helpers
```

Run flow:

1. The frontend logs in and stores an API token.
2. The user selects or creates a session.
3. The user uploads files and sends a message.
4. The backend stores the user message and starts a DeepAgents run.
5. `backend/agents:AGENT` is resolved into prompt, tools, middleware, hooks,
   skills, memory, permissions, and subagents.
6. Runtime events are normalized into UI SSE events and streamed to the browser.
7. The frontend updates the transcript and runtime timeline.
8. Files produced by the `state` backend are exported to uploads and attached to
   the final assistant message.

## Quick Start

### 1. Configure the backend

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

Edit `backend/models.json` and provide credentials through environment
placeholders such as `${OPENAI_API_KEY}`. App settings are read from
`backend/.env`.

Important `.env` settings:

| Setting | Purpose |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Default login credentials. |
| `ADMIN_USERS` | Optional JSON map for multiple users. |
| `UPLOAD_STORAGE_DIR` | Upload and generated-file storage directory. |
| `DEEPAGENTS_MAIN_AGENT` | Main agent import spec. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Model catalog path. Default: `./models.json`. |
| `DEEPAGENTS_DEFAULT_MODEL` | Model ID from the catalog. |
| `DEEPAGENTS_SANDBOX_KIND` | `state`, `filesystem`, `local_shell`, or `custom`. |
| `DEEPAGENTS_SANDBOX_ROOT_DIR` | Root directory for filesystem/local-shell file tools. |
| `DEEPAGENTS_SANDBOX_BACKEND_SPEC` | Import spec for a custom backend factory. |

### 2. Start the backend

```bash
cd backend
uv sync --group dev
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
```

The API defaults to:

- `http://127.0.0.1:8000/api`
- `http://127.0.0.1:8000/health`

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server defaults to `http://127.0.0.1:5173`.

## Model Catalog

`backend/models.json` controls the model selector shown in the UI.

Example:

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
          "model": "gpt-5.4",
          "temperature": null,
          "extra_body": {}
        }
      }
    }
  }
}
```

Provider `options` and model fields are passed to `ChatOpenAI`. Keep
model-specific fields flat in each model entry.

## Agent Package

The default main agent is `backend/agents:AGENT`.

```python
AGENT = {
    "id": "main",
    "name": "deepagents-web",
    "system_prompt": ROOT / "prompts" / "system.md",
    "model": None,
    "workspace": "/workspace/main",
    "tools": TOOLS,
    "builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "hooks": {"run_input": RUN_INPUT_HOOKS, "upload": UPLOAD_HOOKS},
    "skills": SKILLS,
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

`model=None` means the agent uses `DEEPAGENTS_DEFAULT_MODEL`. Subagents can use
the same fields and may set their own `model`, `workspace`, tools, built-in tool
filtering, permissions, skills, memory, and nested subagents.

Use these files first:

- Main prompt: [backend/agents/prompts/system.md](backend/agents/prompts/system.md)
- Tools: [backend/agents/tools](backend/agents/tools)
- Middleware: [backend/agents/middleware](backend/agents/middleware)
- Hooks: [backend/agents/hooks](backend/agents/hooks)
- Skills: [backend/agents/skills](backend/agents/skills)
- Memory: [backend/agents/memory](backend/agents/memory)
- Subagents: [backend/agents/subagents](backend/agents/subagents)

See [backend/agents/README.md](backend/agents/README.md) for package examples.

## Tool Visibility and Permissions

Built-in tools and file permissions are separate controls:

- `builtin_tools` and `disabled_builtin_tools` decide which DeepAgents built-in
  tool names are visible to the model.
- `permissions` decide what visible file tools may do after they are called.

`operations=["read"]` covers `ls`, `read_file`, `glob`, and `grep`.
`operations=["write"]` covers `write_file` and `edit_file`.

Example:

```python
"builtin_tools": ("ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
],
```

## Uploads and Generated Files

- Uploaded files are stored under `UPLOAD_STORAGE_DIR`.
- Upload metadata is passed to run-input hooks.
- In `state` sandbox mode, uploads are copied into virtual `/uploads/...` files.
- Generated or changed state files are exported after run completion.
- Exported files become downloadable assistant-message attachments.

## Sandbox Backends

| Kind | Best for | Notes |
| --- | --- | --- |
| `state` | Safe default virtual file state. | No host shell. Generated files are exported after completion. |
| `filesystem` | File tools over a controlled directory. | Uses virtual paths rooted at `DEEPAGENTS_SANDBOX_ROOT_DIR`. |
| `local_shell` | Trusted local command execution. | Runs commands on the host; restrict tools and users carefully. |
| `custom` | Bring-your-own backend. | Set `DEEPAGENTS_SANDBOX_BACKEND_SPEC`. |

Read [docs/sandbox.md](docs/sandbox.md) before enabling filesystem or shell
access for untrusted users.

## Frontend Behavior

- The transcript follows the bottom while the user is already at the bottom.
- If the user scrolls upward, streaming output does not pull the view down.
- Sending a new user message jumps the transcript back to the bottom.
- Runtime events are batched and capped in the timeline so long tool-heavy runs
  remain responsive.
- Mermaid code blocks render as diagrams. The image-copy button writes PNG data
  through the browser Clipboard API when available.

## Verification

Backend:

```bash
cd backend
uv run ruff check .
uv run pytest
uv run mypy app/core/config.py app/core/runtime_catalog.py app/services/runs.py deepagents_integration
```

Frontend:

```bash
cd frontend
npm run check
```

Repository-level integration tests:

```bash
cd backend
uv run pytest ../tests/backend/test_deepagents_integration.py
```
