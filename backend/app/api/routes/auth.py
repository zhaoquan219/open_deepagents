from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AdminUserDep
from app.core.auth import create_access_token, verify_admin_credentials
from app.core.config import Settings
from app.db.models import AdminUserRecord
from app.schemas.auth import AdminUserResponse, LoginRequest, TokenResponse

router = APIRouter()


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    settings = get_settings(request)
    if not verify_admin_credentials(settings, payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )

    db = request.app.state.database.session_factory()
    try:
        record = (
            db.query(AdminUserRecord)
            .filter(AdminUserRecord.username == payload.username)
            .first()
        )
        if record is None:
            record = AdminUserRecord(
                username=payload.username,
                email=settings.admin_email,
            )
        record.email = settings.admin_email
        record.last_login_at = datetime.now(UTC)
        db.add(record)
        db.commit()
    finally:
        db.close()

    token = create_access_token(settings, payload.username)
    return TokenResponse(
        access_token=token,
        expires_in_seconds=settings.admin_token_expire_minutes * 60,
    )


@router.get("/me", response_model=AdminUserResponse)
def me(username: AdminUserDep) -> AdminUserResponse:
    return AdminUserResponse(username=username)
