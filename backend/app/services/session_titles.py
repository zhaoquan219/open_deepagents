from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import MessageRecord, SessionRecord

DEFAULT_SESSION_TITLE = "New session"
_PLACEHOLDER_SESSION_TITLES = {
    "",
    "new session",
    "new chat",
    "untitled",
    "untitled session",
    "新会话",
    "新聊天",
    "未命名会话",
}


def normalize_session_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_placeholder_session_title(value: str | None) -> bool:
    normalized = normalize_session_title(value)
    return not normalized or normalized.casefold() in _PLACEHOLDER_SESSION_TITLES


def distill_session_title(source: str | None, *, max_length: int = 32) -> str:
    raw = str(source or "")
    lines = [normalize_session_title(line) for line in raw.splitlines()]
    title = next((line for line in lines if line), normalize_session_title(raw))

    if not title:
        return DEFAULT_SESSION_TITLE
    if len(title) <= max_length:
        return title
    return f"{title[: max_length - 3].rstrip()}..."


def sync_session_title_from_source(session: SessionRecord, source: str | None) -> bool:
    if not is_placeholder_session_title(session.title):
        return False

    title = distill_session_title(source)
    if is_placeholder_session_title(title):
        return False

    session.title = title
    return True


def sync_session_title_from_history(db: Session, session: SessionRecord) -> bool:
    if not is_placeholder_session_title(session.title):
        return False

    first_user_message = (
        db.query(MessageRecord.content)
        .filter(
            MessageRecord.session_id == session.id,
            MessageRecord.role == "user",
        )
        .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
        .first()
    )
    if first_user_message is None:
        return False

    return sync_session_title_from_source(session, first_user_message[0])
