from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import AgentRunRecord, MessageRecord, SessionRecord, UploadRecord


def resolve_session_owner(
    *,
    settings: Settings,
    username: str,
) -> str | None:
    if not settings.admin_auth_enabled:
        return None
    return username


def assign_session_owner(session: SessionRecord, username: str, settings: Settings) -> None:
    session.owner_username = resolve_session_owner(settings=settings, username=username)


def is_session_visible_to_user(
    session: SessionRecord,
    *,
    username: str,
    settings: Settings,
) -> bool:
    if not settings.admin_auth_enabled:
        return True
    return session.owner_username == username


def require_session_access(
    session: SessionRecord | None,
    *,
    username: str,
    settings: Settings,
) -> SessionRecord:
    if session is None or not is_session_visible_to_user(
        session,
        username=username,
        settings=settings,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def get_session_for_user(
    db: Session,
    *,
    session_id: str,
    username: str,
    settings: Settings,
    options: Sequence[Any] = (),
) -> SessionRecord:
    query = db.query(SessionRecord)
    for option in options:
        query = query.options(option)
    session = query.filter(SessionRecord.id == session_id).first()
    return require_session_access(session, username=username, settings=settings)


def get_message_for_user(
    db: Session,
    *,
    message_id: str,
    username: str,
    settings: Settings,
) -> MessageRecord:
    message = db.query(MessageRecord).filter(MessageRecord.id == message_id).first()
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    get_session_for_user(
        db,
        session_id=message.session_id,
        username=username,
        settings=settings,
    )
    return message


def get_upload_for_user(
    db: Session,
    *,
    upload_id: str,
    username: str,
    settings: Settings,
) -> UploadRecord:
    upload = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    get_session_for_user(
        db,
        session_id=upload.session_id,
        username=username,
        settings=settings,
    )
    return upload


def get_run_for_user(
    db: Session,
    *,
    run_id: str,
    username: str,
    settings: Settings,
) -> AgentRunRecord:
    run = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    get_session_for_user(
        db,
        session_id=run.session_id,
        username=username,
        settings=settings,
    )
    return run
