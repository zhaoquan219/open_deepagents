from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import UserRecord
from app.settings import Settings

security = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 390_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = encoded.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
    return hmac.compare_digest(candidate, digest_hex)


def sync_configured_users(db: Session, settings: Settings) -> None:
    for username, password in settings.configured_users().items():
        user = db.scalar(select(UserRecord).where(UserRecord.username == username))
        if user is None:
            db.add(
                UserRecord(
                    username=username,
                    password_hash=hash_password(password),
                    email=settings.admin_email if username == settings.admin_username else None,
                    role="admin" if username == settings.admin_username else "user",
                )
            )
            continue
        if not user.is_active:
            continue
        user.password_hash = hash_password(password)
        if username == settings.admin_username:
            user.email = settings.admin_email


def create_token(username: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.admin_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.admin_token_secret, algorithm="HS256")


def decode_token(token: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(token, settings.admin_token_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return username


def get_db(request: Request) -> Generator[Session]:
    with request.app.state.database.session() as db:
        yield db


DbSession = Annotated[Session, Depends(get_db)]


def current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> UserRecord:
    settings: Settings = request.app.state.settings
    if not settings.admin_auth_enabled:
        user = db.scalar(select(UserRecord).where(UserRecord.username == settings.admin_username))
        if user is None:
            sync_configured_users(db, settings)
            user = db.scalar(
                select(UserRecord).where(UserRecord.username == settings.admin_username)
            )
        assert user is not None
        return user
    token = credentials.credentials if credentials else request.query_params.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    username = decode_token(token, settings)
    user = db.scalar(select(UserRecord).where(UserRecord.username == username))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user


CurrentUser = Annotated[UserRecord, Depends(current_user)]
