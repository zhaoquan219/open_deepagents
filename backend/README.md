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
