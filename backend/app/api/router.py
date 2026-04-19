from fastapi import APIRouter

from app.api.routes import auth, messages, runs, runtime, sessions, uploads

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/admin", tags=["admin"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(messages.router, tags=["messages"])
api_router.include_router(runs.router, tags=["runs"])
api_router.include_router(runtime.router, tags=["runtime"])
api_router.include_router(uploads.router, tags=["uploads"])
