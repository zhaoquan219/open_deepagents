from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth import decode_access_token
from app.core.config import Settings
from app.core.database import DatabaseState
from app.storage import LocalStorage

bearer_scheme = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> DatabaseState:
    return cast(DatabaseState, request.app.state.database)


def get_db(request: Request) -> Iterator[Session]:
    database = get_database(request)
    yield from database.session()


def get_storage(request: Request) -> LocalStorage:
    settings = get_settings(request)
    return LocalStorage(settings.upload_storage_dir)


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = get_settings(request)
    if not settings.admin_auth_enabled:
        return settings.admin_username

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )

    return decode_access_token(settings, credentials.credentials)


AdminUserDep = Annotated[str, Depends(require_admin)]
DatabaseStateDep = Annotated[DatabaseState, Depends(get_database)]
DatabaseSessionDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[LocalStorage, Depends(get_storage)]
