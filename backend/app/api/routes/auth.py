from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import require_admin
from app.core.auth import create_access_token, verify_admin_credentials
from app.core.config import Settings
from app.schemas.auth import AdminUserResponse, LoginRequest, TokenResponse


router = APIRouter()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    settings = get_settings(request)
    if not verify_admin_credentials(settings, payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )

    token = create_access_token(settings, payload.username)
    return TokenResponse(
        access_token=token,
        expires_in_seconds=settings.admin_token_expire_minutes * 60,
    )


@router.get("/me", response_model=AdminUserResponse)
def me(username: str = Depends(require_admin)) -> AdminUserResponse:
    return AdminUserResponse(username=username)
