from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, status

from app.core.config import Settings

ALGORITHM = "HS256"


def verify_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    configured_password = settings.admin_users.get(username)
    if configured_password is not None:
        return password == configured_password
    return username == settings.admin_username and password == settings.admin_password


def create_access_token(settings: Settings, subject: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.admin_token_expire_minutes)
    payload = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, settings.admin_token_secret, algorithm=ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> str:
    try:
        payload = jwt.decode(token, settings.admin_token_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token",
        ) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token subject",
        )

    return subject
