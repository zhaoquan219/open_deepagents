## Backend scaffold

FastAPI backend foundation for the DeepAgents agent platform scaffold.

### Included in this lane

- FastAPI app shell
- single-admin bearer auth
- session/message CRUD
- upload metadata + local file persistence
- SQLAlchemy models compatible with MySQL-backed deployments

### Local development

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

### Environment

Copy `backend/.env.example` to `.env` and adjust as needed.
