# open_deepagents

`open_deepagents` is a runnable web console for teams building internal
DeepAgents applications. It gives you the application shell around an agent:
admin login, session history, uploads, streamed run events, a Vue chat UI, model
selection, subagent traces, and a file-backed agent package you can customize.

Chinese documentation: [README_CH.md](README_CH.md)

![DeepAgents web workspace screenshot](docs/images/workspace-en.png)

## How It Fits Together

```text
frontend/  Vue 3 console: login, sessions, chat, uploads, Mermaid, run timeline
backend/   FastAPI service: auth, persistence, uploads, run orchestration
agents/    Agent package: prompt, tools, middleware, hooks, skills, memory, subagents
models.json Model catalog: providers, model IDs, OpenAI-compatible options
```

Request flow:

1. The frontend logs in and stores a bearer token.
2. A user opens a session, optionally uploads files, and sends a prompt.
3. The backend stores the user message and starts a DeepAgents run.
4. `backend/agents:AGENT` is resolved into tools, hooks, skills, memory, and subagents.
5. Runtime events are normalized into the UI SSE envelope and streamed back.
6. The frontend updates the transcript and timeline, including tool/subagent status.
7. Files produced in the `state` backend are exported into `backend/data/uploads`
   and attached to the assistant response.

## Repository Layout

```text
.
├── backend/
│   ├── agents/                       Main recursive agent package
│   │   ├── prompts/system.md          Main system prompt
│   │   ├── tools/                     LangChain/DeepAgents tools
│   │   ├── middleware/                Runtime middleware
│   │   ├── hooks/                     Run-input and upload hooks
│   │   ├── skills/                    DeepAgents skills
│   │   ├── memory/                    Markdown memory files
│   │   └── subagents/                 Nested agent packages
│   ├── app/                          FastAPI application code
│   ├── deepagents_integration/       Thin DeepAgents runtime bridge
│   ├── models.example.json           Model catalog template
│   └── tests/                        Backend tests
├── frontend/
│   └── src/                          Vue UI, API client, stores, copy
├── packages/contracts/               Shared event contracts
├── docs/                             Extra documentation and screenshots
├── tests/                            Repository-level tests
└── verification/                     Audit and contract checks
```

There is no `backend/extensions/` architecture anymore. Custom behavior belongs
under `backend/agents/` or in a separate importable module referenced from the
agent package.

## Quick Start

### 1. Configure the Backend

```bash
cp backend/.env.example backend/.env
cp backend/models.example.json backend/models.json
```

Edit `backend/models.json` and provide real provider credentials, usually via
environment-variable placeholders such as `${OPENAI_API_KEY}`. The backend only
reads `backend/.env` for app settings.

Important settings:

| Variable | Purpose |
| --- | --- |
| `DEEPAGENTS_MAIN_AGENT` | Import spec for the main agent package. Default: `agents:AGENT`. |
| `DEEPAGENTS_MODEL_CONFIG_PATH` | Path to the model catalog. Default: `./models.json`. |
| `DEEPAGENTS_DEFAULT_MODEL` | Model ID from the catalog, such as `openai/gpt-5-4`. |
| `DEEPAGENTS_AGENT_NAME` | Name passed to `create_deep_agent`. |
| `DEEPAGENTS_BUILTIN_TOOLS` | Optional allowlist for DeepAgents built-ins. |
| `DEEPAGENTS_DISABLED_BUILTIN_TOOLS` | Optional blocklist for DeepAgents built-ins. |
| `DEEPAGENTS_SANDBOX_*` | Sandbox backend selection and safety settings. |
| `UPLOAD_STORAGE_DIR` | Upload/output artifact directory. Default resolves to `backend/data/uploads`. |

Legacy settings such as `DEEPAGENTS_MODEL`, `CUSTOM_API_*`,
`DEEPAGENTS_TOOL_SPECS`, `DEEPAGENTS_MIDDLEWARE_SPECS`,
`DEEPAGENTS_RUN_INPUT_HOOK_SPECS`, and `DEEPAGENTS_UPLOAD_HOOK_SPECS` are not
part of the current architecture. Move those concerns into `models.json` or
`backend/agents/`.

### 2. Install and Start

Backend:

```bash
cd backend
uv sync --group dev
uv run python -m app.db.manage init
uv run uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Default URLs:

- API: `http://127.0.0.1:8000/api`
- Health check: `http://127.0.0.1:8000/health`
- Frontend dev server: `http://127.0.0.1:5173`

Default login comes from `ADMIN_USERNAME` and `ADMIN_PASSWORD` in
`backend/.env`.

## Agent Package Guide

The main agent is `backend/agents:AGENT`. It is a plain Python mapping:

```python
AGENT = {
    "id": "main",
    "name": "deepagents-web",
    "system_prompt": ROOT / "prompts" / "system.md",
    "model": None,
    "tools": TOOLS,
    "middleware": MIDDLEWARE,
    "hooks": {"run_input": RUN_INPUT_HOOKS, "upload": UPLOAD_HOOKS},
    "skills": SKILLS,
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

`model=None` means “use `DEEPAGENTS_DEFAULT_MODEL`”. A subagent may set its own
`model` or inherit the selected default. Sandbox selection is global; agents and
subagents do not define their own sandbox.

Use these package files first:

- Main prompt: [backend/agents/prompts/system.md](backend/agents/prompts/system.md)
- Tool exports: [backend/agents/tools/__init__.py](backend/agents/tools/__init__.py)
- Middleware exports: [backend/agents/middleware/__init__.py](backend/agents/middleware/__init__.py)
- Run/upload hooks: [backend/agents/hooks/__init__.py](backend/agents/hooks/__init__.py)
- Skills selection: [backend/agents/skills/__init__.py](backend/agents/skills/__init__.py)
- Memory selection: [backend/agents/memory/__init__.py](backend/agents/memory/__init__.py)
- Subagents: [backend/agents/subagents](backend/agents/subagents)

Selections support `*`, one string, or a list. See
[backend/agents/README.md](backend/agents/README.md) for examples.

## Model Catalog

`backend/models.json` defines providers and model choices. The frontend reads
`GET /api/runtime/options` and shows the available models in the composer.

Example shape:

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

Provider `options` are passed to `ChatOpenAI` with secrets resolved from the
environment. Keep model-specific options flat inside each model entry.

## Uploads, State Files, and Downloads

Uploads are stored under `UPLOAD_STORAGE_DIR`. During a run:

- user uploads are attached to the user message;
- the run-input hook describes uploaded files to the model;
- in `state` sandbox mode, uploaded files are copied into virtual `/uploads/...`
  files for DeepAgents tools;
- generated state files are exported back into `UPLOAD_STORAGE_DIR` and attached
  to the final assistant message;
- frontend attachment chips include authenticated download links.

## Sandbox Safety

Supported sandbox kinds:

| Kind | Use when | Notes |
| --- | --- | --- |
| `state` | Default virtual file state. | No host shell. Generated files are exported after completion. |
| `filesystem` | File tools need a real directory. | Defaults to `backend/data`; virtual path mode is forced. |
| `local_shell` | Trusted local command execution. | Host execution; pair with tool filtering. |
| `custom` | You provide a backend factory. | Set `DEEPAGENTS_SANDBOX_BACKEND_SPEC`. |

See [docs/sandbox.md](docs/sandbox.md) for path behavior and safety details.

## Verification

From the repository root:

```bash
PYTHONPATH=.:backend backend/.venv/bin/pytest -q
backend/.venv/bin/ruff check backend tests
cd frontend && npm run check
```

For focused backend typing on the production code touched most often:

```bash
backend/.venv/bin/mypy backend/app backend/deepagents_integration
```

`mypy backend` also checks tests and bundled skill scripts; that broader target
is stricter than the current test contract and may report unrelated typing debt.
