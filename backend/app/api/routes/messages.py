from fastapi import APIRouter, status

from app.api.deps import AdminUserDep, DatabaseSessionDep, SettingsDep
from app.core.session_scope import get_message_for_user, get_session_for_user
from app.db.models import MessageRecord
from app.schemas.message import MessageCreate, MessageRead, MessageUpdate
from app.services.session_titles import sync_session_title_from_source

router = APIRouter()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
def list_messages(
    session_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> list[MessageRecord]:
    get_session_for_user(db, session_id=session_id, username=username, settings=settings)
    return list(
        db.query(MessageRecord)
        .filter(MessageRecord.session_id == session_id)
        .order_by(MessageRecord.created_at.asc())
        .all()
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    session_id: str,
    payload: MessageCreate,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> MessageRecord:
    session = get_session_for_user(
        db,
        session_id=session_id,
        username=username,
        settings=settings,
    )
    record = MessageRecord(
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        is_final=payload.is_final,
        run_id=payload.run_id,
        step_id=payload.step_id,
        extra=payload.extra,
    )
    session.last_run_id = payload.run_id or session.last_run_id
    if payload.role == "user":
        sync_session_title_from_source(session, payload.content)
    db.add(record)
    db.add(session)
    db.commit()
    db.refresh(record)
    return record


@router.get("/messages/{message_id}", response_model=MessageRead)
def get_message(
    message_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> MessageRecord:
    return get_message_for_user(db, message_id=message_id, username=username, settings=settings)


@router.patch("/messages/{message_id}", response_model=MessageRead)
def update_message(
    message_id: str,
    payload: MessageUpdate,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> MessageRecord:
    record = get_message_for_user(db, message_id=message_id, username=username, settings=settings)

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field_name, value)

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(
    message_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> None:
    record = get_message_for_user(db, message_id=message_id, username=username, settings=settings)
    db.delete(record)
    db.commit()
