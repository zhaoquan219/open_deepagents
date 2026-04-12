from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth import decode_access_token
from app.core.config import Settings
from app.core.database import DatabaseState
from app.services.storage import LocalStorage


bearer_scheme = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> DatabaseState:
    return request.app.state.database


def get_db(request: Request) -> Session:
    database = get_database(request)
    return next(database.session())


def get_storage(request: Request) -> LocalStorage:
    settings = get_settings(request)
    return LocalStorage(settings.upload_storage_dir)


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )

    settings = get_settings(request)
    return decode_access_token(settings, credentials.credentials)
