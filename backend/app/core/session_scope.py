from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.core.logging import format_log_message
from app.db.models import AgentRunRecord, MessageRecord, SessionRecord, UploadRecord, app_now

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
logger = logging.getLogger(__name__)
PromptMessageType = Literal["message", "prompt_injection"]
PromptInjectionVisibility = Literal["visible", "hidden"]
PromptInjectionPosition = Literal[
    "before_system",
    "after_system",
    "before_user",
    "after_user",
]

MESSAGE_TYPE_MESSAGE: PromptMessageType = "message"
MESSAGE_TYPE_PROMPT_INJECTION: PromptMessageType = "prompt_injection"
DEFAULT_INJECTION_POSITION: PromptInjectionPosition = "before_user"
DEFAULT_INJECTION_ROLE = "system"
VALID_INJECTION_POSITIONS = {
    "before_system",
    "after_system",
    "before_user",
    "after_user",
}
VALID_INJECTION_VISIBILITIES = {"visible", "hidden"}
LEGACY_INJECTION_POSITION_MAP: dict[str, PromptInjectionPosition] = {
    "before_history": "before_system",
    "before_run_prompt": "before_user",
    "after_run_prompt": "after_user",
    "after_history": "after_user",
}


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


def new_runtime_thread_id() -> str:
    return f"thread-{uuid4()}"


def ensure_runtime_thread_id(db: Session, session: SessionRecord) -> str:
    if session.runtime_thread_id:
        return session.runtime_thread_id
    session.runtime_thread_id = new_runtime_thread_id()
    db.add(session)
    db.flush()
    return session.runtime_thread_id


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


@dataclass(frozen=True)
class PromptInjectionDraft:
    session_id: str
    content: str
    run_id: str | None
    role: str
    visibility: PromptInjectionVisibility
    position: PromptInjectionPosition
    source: str | None
    extra: dict[str, Any] = field(default_factory=dict)


def is_prompt_injection(record: MessageRecord) -> bool:
    return str(record.message_type or MESSAGE_TYPE_MESSAGE) == MESSAGE_TYPE_PROMPT_INJECTION


def is_transcript_visible(record: MessageRecord, *, include_hidden: bool = False) -> bool:
    _ = include_hidden
    return not is_prompt_injection(record)


def normalize_injection_position(position: str | None) -> PromptInjectionPosition:
    if position in VALID_INJECTION_POSITIONS:
        return cast(PromptInjectionPosition, position)
    if position in LEGACY_INJECTION_POSITION_MAP:
        return LEGACY_INJECTION_POSITION_MAP[position]
    return DEFAULT_INJECTION_POSITION


class PromptInjectionService:
    def __init__(self, database: DatabaseState) -> None:
        self.database = database

    def inject_prompt(
        self,
        *,
        session_id: str,
        content: str,
        run_id: str | None = None,
        position: PromptInjectionPosition = DEFAULT_INJECTION_POSITION,
        visibility: PromptInjectionVisibility | None = None,
        show_in_ui: bool = False,
        source: str | None = "backend",
        persist: bool = True,
        role: str = DEFAULT_INJECTION_ROLE,
        extra: dict[str, Any] | None = None,
        db: Session | None = None,
    ) -> MessageRecord | PromptInjectionDraft:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Prompt injection content cannot be empty")
        normalized_position = normalize_injection_position(position)
        if position != normalized_position:
            raise ValueError(f"Unsupported prompt injection position: {position}")
        resolved_visibility: PromptInjectionVisibility = (
            visibility if visibility is not None else ("visible" if show_in_ui else "hidden")
        )
        if resolved_visibility not in VALID_INJECTION_VISIBILITIES:
            raise ValueError(f"Unsupported prompt injection visibility: {visibility}")

        payload_extra = {
            **(extra or {}),
            "source": source,
            "visibility": resolved_visibility,
            "position": normalized_position,
        }
        if not persist:
            return PromptInjectionDraft(
                session_id=session_id,
                content=normalized_content,
                run_id=run_id,
                role=role,
                visibility=resolved_visibility,
                position=normalized_position,
                source=source,
                extra=payload_extra,
            )

        owns_session = db is None
        active_db = db or self.database.session_factory()
        try:
            session = require_session_record(active_db, session_id=session_id)
            record = MessageRecord(
                session_id=session_id,
                role=role,
                content=normalized_content,
                run_id=run_id,
                is_final=True,
                message_type=MESSAGE_TYPE_PROMPT_INJECTION,
                visibility=resolved_visibility,
                source=source,
                injection_position=normalized_position,
                extra=payload_extra,
            )
            session.updated_at = app_now()
            if run_id:
                session.last_run_id = run_id
            active_db.add(record)
            active_db.add(session)
            active_db.flush()
            if owns_session:
                active_db.commit()
                active_db.refresh(record)
            logger.info(
                "%s",
                format_log_message(
                    "prompt injection persisted",
                    event="prompt_injection.created",
                    phase="persisting prompt injection",
                    session_id=session_id,
                    run_id=run_id or "",
                    source=source or "",
                    visibility=resolved_visibility,
                    position=normalized_position,
                    role=role,
                ),
            )
            return record
        except Exception:
            if owns_session:
                active_db.rollback()
            raise
        finally:
            if owns_session:
                active_db.close()

    def append_injection(self, **kwargs: Any) -> MessageRecord | PromptInjectionDraft:
        return self.inject_prompt(**kwargs)

    def list_transcript_records(
        self,
        *,
        session_id: str,
        include_hidden: bool = False,
        db: Session | None = None,
    ) -> list[MessageRecord]:
        owns_session = db is None
        active_db = db or self.database.session_factory()
        try:
            query = active_db.query(MessageRecord).filter(MessageRecord.session_id == session_id)
            if not include_hidden:
                query = query.filter(MessageRecord.message_type != MESSAGE_TYPE_PROMPT_INJECTION)
            return list(
                query.order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc()).all()
            )
        finally:
            if owns_session:
                active_db.close()

    def assemble_prompt_messages(
        self,
        *,
        session_id: str,
        run_id: str,
        db: Session | None = None,
    ) -> list[MessageRecord]:
        owns_session = db is None
        active_db = db or self.database.session_factory()
        try:
            records = list(
                active_db.query(MessageRecord)
                .filter(
                    MessageRecord.session_id == session_id,
                    or_(
                        MessageRecord.message_type != MESSAGE_TYPE_PROMPT_INJECTION,
                        MessageRecord.run_id.is_(None),
                        MessageRecord.run_id == run_id,
                    ),
                )
                .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
                .all()
            )
            return _assemble_prompt_records(records=records, run_id=run_id)
        finally:
            if owns_session:
                active_db.close()


def require_session_record(db: Session, *, session_id: str) -> SessionRecord:
    session = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    return session


def _assemble_prompt_records(
    *,
    records: Sequence[MessageRecord],
    run_id: str,
) -> list[MessageRecord]:
    system_records: list[MessageRecord] = []
    conversation_records: list[MessageRecord] = []
    grouped_injections: dict[str, list[MessageRecord]] = {
        "before_system": [],
        "after_system": [],
        "before_user": [],
        "after_user": [],
    }
    for record in records:
        if is_prompt_injection(record):
            if record.run_id not in {None, run_id} or not (record.content or "").strip():
                continue
            grouped_injections[normalize_injection_position(record.injection_position)].append(record)
            continue
        if not (record.content or "").strip():
            continue
        if record.role == "system":
            system_records.append(record)
        elif record.role in {"user", "assistant"}:
            conversation_records.append(record)

    assembled = [
        *grouped_injections["before_system"],
        *system_records,
        *grouped_injections["after_system"],
    ]
    current_user_index = next(
        (
            index
            for index, record in enumerate(conversation_records)
            if record.role == "user" and record.run_id == run_id
        ),
        None,
    )
    if current_user_index is None:
        return [
            *assembled,
            *conversation_records,
            *grouped_injections["before_user"],
            *grouped_injections["after_user"],
        ]
    return [
        *assembled,
        *conversation_records[:current_user_index],
        *grouped_injections["before_user"],
        conversation_records[current_user_index],
        *grouped_injections["after_user"],
        *conversation_records[current_user_index + 1 :],
    ]
