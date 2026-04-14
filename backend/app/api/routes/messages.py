from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import DatabaseSessionDep, require_admin
from app.db.models import MessageRecord, SessionRecord
from app.schemas.message import MessageCreate, MessageRead, MessageUpdate
from app.services.session_titles import sync_session_title_from_source

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
def list_messages(session_id: str, db: DatabaseSessionDep) -> list[MessageRecord]:
    ensure_session_exists(db, session_id)
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
) -> MessageRecord:
    session = ensure_session_exists(db, session_id)
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
def get_message(message_id: str, db: DatabaseSessionDep) -> MessageRecord:
    record = db.query(MessageRecord).filter(MessageRecord.id == message_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return record


@router.patch("/messages/{message_id}", response_model=MessageRead)
def update_message(
    message_id: str,
    payload: MessageUpdate,
    db: DatabaseSessionDep,
) -> MessageRecord:
    record = db.query(MessageRecord).filter(MessageRecord.id == message_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field_name, value)

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(message_id: str, db: DatabaseSessionDep) -> None:
    record = db.query(MessageRecord).filter(MessageRecord.id == message_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    db.delete(record)
    db.commit()


def ensure_session_exists(db: DatabaseSessionDep, session_id: str) -> SessionRecord:
    record = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return record
