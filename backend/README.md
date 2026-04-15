## Backend scaffold

FastAPI backend foundation for the DeepAgents agent platform scaffold.

### Included in this lane

- FastAPI app shell
- single-admin bearer auth
- session/message CRUD
- upload metadata + local file persistence
- SQLAlchemy models compatible with MySQL-backed deployments
- project-managed extension templates under `backend/extensions/`
- runtime hook templates for upload and run-input customization

### Local development

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

### Environment

Copy `backend/.env.example` to `.env` and adjust as needed.
The runtime system prompt is managed in `backend/prompts/deepagents-system-prompt.md`.
`backend/.env` is the only env file used by the backend runtime.
The default template already enables the unified tool, middleware, runtime hook
entrypoints, the default skills directory, and the sample sandbox settings.
Tools are aggregated from `extensions.tools:TOOLS`.
Middleware is aggregated from `extensions.middleware:MIDDLEWARE`.
See `backend/extensions/tools/README.md` and
`backend/extensions/middleware/README.md` for extension authoring examples.
Runtime hooks are loaded from `backend/extensions/runtime_hooks/__init__.py`
with `DEEPAGENTS_RUN_INPUT_HOOK_SPECS` and `DEEPAGENTS_UPLOAD_HOOK_SPECS`.
If either spec is blank, that hook lane has no effect.
Skills are loaded from `DEEPAGENTS_SKILLS` by scanning subdirectories for
`SKILL.md`, and the backend routes those skill paths to disk even when the main
runtime backend is `state` or a sandbox rooted elsewhere.
The default sandbox permissions are enforced in code and only allow read access
to `backend/data/` and `backend/extensions/skills/`.
Uploaded files are stored at `UPLOAD_STORAGE_DIR/<session_id>/<uuid>-<filename>`.
With the default configuration that resolves to `backend/data/uploads/...`, and if
you point `UPLOAD_STORAGE_DIR` somewhere else the backend automatically adds that
directory to the runtime read permissions.
If `CUSTOM_API_KEY`, `CUSTOM_API_URL`, and `CUSTOM_API_MODEL` are all set,
the backend builds a `ChatOpenAI` client for that endpoint.
`CUSTOM_API_TEMPERATURE` is optional and omitted entirely when unset.
`CUSTOM_API_DEFAULT_HEADERS` accepts a JSON object string and also supports
comma-separated `KEY=VALUE` pairs for compatibility.

### Runtime hooks

Hook entrypoints use the same `path/to/file.py:OBJECT_NAME` import-spec format
as tools and middleware.

Recommended starting config:

```dotenv
DEEPAGENTS_RUN_INPUT_HOOK_SPECS=extensions.runtime_hooks:RUN_INPUT_HOOKS
DEEPAGENTS_UPLOAD_HOOK_SPECS=extensions.runtime_hooks:UPLOAD_HOOKS
```

Then edit `backend/extensions/runtime_hooks/attachment_hooks.py`.

- Run-input hooks receive a context with `session_id`, `run_id`, `role`,
  `content`, `attachments`, and `is_current_run`. Return a string or
  `{"content": "..."}` to replace the content sent to DeepAgents.
- Upload hooks receive file metadata and the raw payload after storage. Return a
  mapping to merge into `UploadRecord.extra`.

### Middleware context

DeepAgents runs receive a typed runtime context with `session_id`, `run_id`, and
`current_attachments`. Function-style middleware can read it from
`runtime.context` in decorators such as `@before_agent`, or from
`request.runtime.context` in wrappers such as `@wrap_tool_call`.

### Built-in tool filtering

DeepAgents injects built-in tools such as `ls`, `read_file`, `write_file`,
`edit_file`, `grep`, `glob`, `execute`, `task`, and `write_todos`. Operators can
filter those without removing custom tools:

```dotenv
DEEPAGENTS_BUILTIN_TOOLS=write_todos,ls,read_file,glob,grep,task
DEEPAGENTS_DISABLED_BUILTIN_TOOLS=execute,write_file,edit_file
```

If the allowlist is set, built-in tools outside it are hidden first. The
blocklist then hides matching built-ins from the remaining tool set. Custom tools
loaded from `DEEPAGENTS_TOOL_SPECS` pass through unchanged.
