from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db, require_admin
from app.db.models import SessionRecord
from app.schemas.session import SessionCreate, SessionDetail, SessionRead, SessionUpdate


router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("", response_model=list[SessionRead])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionRecord]:
    return list(
        db.query(SessionRecord)
        .order_by(SessionRecord.updated_at.desc(), SessionRecord.created_at.desc())
        .all()
    )


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> SessionRecord:
    record = SessionRecord(
        title=payload.title,
        runtime_thread_id=payload.runtime_thread_id,
        extra=payload.extra,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionRecord:
    record = (
        db.query(SessionRecord)
        .options(
            selectinload(SessionRecord.messages),
            selectinload(SessionRecord.uploads),
        )
        .filter(SessionRecord.id == session_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return record


@router.patch("/{session_id}", response_model=SessionRead)
def update_session(
    session_id: str,
    payload: SessionUpdate,
    db: Session = Depends(get_db),
) -> SessionRecord:
    record = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field_name, value)

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> None:
    record = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    db.delete(record)
    db.commit()
