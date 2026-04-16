from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import AdminUserDep, DatabaseSessionDep, SettingsDep, StorageDep
from app.core.session_scope import (
    get_message_for_user,
    get_session_for_user,
    get_upload_for_user,
)
from app.db.models import UploadRecord
from app.schemas.upload import UploadRead
from deepagents_integration.run_hooks import apply_upload_hooks, build_upload_hook_context

router = APIRouter()
UploadFileDep = Annotated[UploadFile, File()]
MessageIdForm = Annotated[str | None, Form()]
PendingUploadCleanup = Callable[[Session, UploadRecord], None]
PENDING_UPLOAD_CLEANUPS: tuple[PendingUploadCleanup, ...] = ()


@router.get("/sessions/{session_id}/uploads", response_model=list[UploadRead])
def list_uploads(
    session_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> list[UploadRecord]:
    get_session_for_user(db, session_id=session_id, username=username, settings=settings)
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
    file: UploadFileDep,
    db: DatabaseSessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    username: AdminUserDep,
    message_id: MessageIdForm = None,
) -> UploadRecord:
    get_session_for_user(db, session_id=session_id, username=username, settings=settings)
    if message_id is not None:
        message = get_message_for_user(
            db,
            message_id=message_id,
            username=username,
            settings=settings,
        )
        if message.session_id != session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid message binding",
            )

    payload = await file.read()
    if len(payload) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large",
        )

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
    db.flush()
    hook_extra = apply_upload_hooks(
        context=build_upload_hook_context(
            upload_id=record.id,
            session_id=session_id,
            message_id=message_id,
            filename=record.filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            storage_key=record.storage_key,
            sha256=record.sha256,
            upload_root=settings.upload_storage_dir,
            payload=payload,
        ),
        hook_specs=settings.upload_hook_specs(),
    )
    if hook_extra:
        record.extra = {**(record.extra or {}), **hook_extra}
        db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.post("/uploads", response_model=UploadRead, status_code=status.HTTP_201_CREATED)
async def upload_file_global(
    session_id: Annotated[str, Form()],
    file: UploadFileDep,
    db: DatabaseSessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    username: AdminUserDep,
    message_id: MessageIdForm = None,
) -> UploadRecord:
    return await upload_file(
        session_id=session_id,
        file=file,
        db=db,
        storage=storage,
        settings=settings,
        username=username,
        message_id=message_id,
    )


@router.get("/uploads/{upload_id}", response_model=UploadRead)
def get_upload(
    upload_id: str,
    db: DatabaseSessionDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> UploadRecord:
    return get_upload_for_user(db, upload_id=upload_id, username=username, settings=settings)


@router.get("/uploads/{upload_id}/content")
def download_upload(
    upload_id: str,
    db: DatabaseSessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> FileResponse:
    record = get_upload_for_user(db, upload_id=upload_id, username=username, settings=settings)
    file_path = storage.resolve(record.storage_key)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload content missing")
    return FileResponse(
        path=file_path,
        filename=record.filename,
        media_type=record.content_type,
    )


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(
    upload_id: str,
    db: DatabaseSessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    username: AdminUserDep,
) -> None:
    record = get_upload_for_user(db, upload_id=upload_id, username=username, settings=settings)
    if record.message_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sent uploads cannot be deleted",
        )

    cleanup_pending_upload_state(db=db, record=record)
    storage.delete(record.storage_key)
    db.delete(record)
    db.commit()


def cleanup_pending_upload_state(*, db: Session, record: UploadRecord) -> None:
    for cleanup in PENDING_UPLOAD_CLEANUPS:
        cleanup(db, record)
