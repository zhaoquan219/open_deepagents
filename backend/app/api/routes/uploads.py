from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_storage, require_admin
from app.db.models import MessageRecord, SessionRecord, UploadRecord
from app.schemas.upload import UploadRead
from app.services.storage import LocalStorage


router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/sessions/{session_id}/uploads", response_model=list[UploadRead])
def list_uploads(session_id: str, db: Session = Depends(get_db)) -> list[UploadRecord]:
    ensure_session_exists(db, session_id)
    return list(
        db.query(UploadRecord)
        .filter(UploadRecord.session_id == session_id)
        .order_by(UploadRecord.created_at.asc())
        .all()
    )


@router.post(
    "/sessions/{session_id}/uploads",
    response_model=UploadRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    message_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> UploadRecord:
    ensure_session_exists(db, session_id)
    if message_id is not None:
        message = db.query(MessageRecord).filter(MessageRecord.id == message_id).first()
        if message is None or message.session_id != session_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message binding")

    payload = await file.read()
    max_upload_size_bytes = 10 * 1024 * 1024
    if len(payload) > max_upload_size_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    storage_key, digest = storage.save_bytes(
        session_id=session_id,
        filename=file.filename or "upload.bin",
        payload=payload,
    )
    record = UploadRecord(
        session_id=session_id,
        message_id=message_id,
        filename=Path(file.filename or "upload.bin").name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(payload),
        storage_key=storage_key,
        sha256=digest,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/uploads/{upload_id}", response_model=UploadRead)
def get_upload(upload_id: str, db: Session = Depends(get_db)) -> UploadRecord:
    record = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return record


@router.get("/uploads/{upload_id}/content")
def download_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> FileResponse:
    record = get_upload(upload_id, db)
    file_path = storage.resolve(record.storage_key)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload content missing")
    return FileResponse(path=file_path, filename=record.filename, media_type=record.content_type)


def ensure_session_exists(db: Session, session_id: str) -> SessionRecord:
    record = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return record
