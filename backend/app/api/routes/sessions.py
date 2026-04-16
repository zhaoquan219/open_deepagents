from fastapi import APIRouter, status
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUserDep, DatabaseSessionDep, SettingsDep
from app.core.session_scope import (
    assign_session_owner,
    get_session_for_user,
)
from app.db.models import SessionRecord
from app.schemas.session import SessionCreate, SessionDetail, SessionRead, SessionUpdate
from app.services.session_titles import sync_session_title_from_history

router = APIRouter()


@router.get("", response_model=list[SessionRead])
def list_sessions(
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> list[SessionRecord]:
    query = db.query(SessionRecord)
    if settings.admin_auth_enabled:
        query = query.filter(SessionRecord.owner_username == username)
    records = list(
        query.order_by(SessionRecord.updated_at.desc(), SessionRecord.created_at.desc()).all()
    )
    changed = False
    for record in records:
        changed = sync_session_title_from_history(db, record) or changed
    if changed:
        db.commit()
    return records


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> SessionRecord:
    record = SessionRecord(
        title=payload.title,
        runtime_thread_id=payload.runtime_thread_id,
        extra=payload.extra,
    )
    assign_session_owner(record, username, settings)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> SessionRecord:
    record = get_session_for_user(
        db,
        session_id=session_id,
        username=username,
        settings=settings,
        options=(
            selectinload(SessionRecord.messages),
            selectinload(SessionRecord.uploads),
        ),
    )
    if sync_session_title_from_history(db, record):
        db.commit()
        db.refresh(record)
    return record


@router.patch("/{session_id}", response_model=SessionRead)
def update_session(
    session_id: str,
    payload: SessionUpdate,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> SessionRecord:
    record = get_session_for_user(
        db,
        session_id=session_id,
        username=username,
        settings=settings,
    )

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field_name, value)

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> None:
    record = get_session_for_user(
        db,
        session_id=session_id,
        username=username,
        settings=settings,
    )

    db.delete(record)
    db.commit()
