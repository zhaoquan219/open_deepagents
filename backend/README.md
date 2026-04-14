## Backend scaffold

FastAPI backend foundation for the DeepAgents agent platform scaffold.

### Included in this lane

- FastAPI app shell
- single-admin bearer auth
- session/message CRUD
- upload metadata + local file persistence
- SQLAlchemy models compatible with MySQL-backed deployments
- project-managed extension templates under `backend/extensions/`

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
The default template already enables the sample tool, middleware, skills, and sandbox settings.
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
