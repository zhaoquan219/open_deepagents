import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import AdminUserDep, DatabaseSessionDep, SettingsDep, StorageDep
from app.core.logging import format_log_message
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
logger = logging.getLogger(__name__)


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
            logger.warning(
                "%s",
                format_log_message(
                    "upload rejected because the target message belongs to a different session",
                    event="upload.invalid_message_binding",
                    phase="validating upload request",
                    session_id=session_id,
                    message_id=message_id,
                    username=username,
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid message binding",
            )

    payload = await file.read()
    if len(payload) > settings.max_upload_size_bytes:
        logger.warning(
            "%s",
            format_log_message(
                "upload rejected because the file is larger than the configured limit",
                event="upload.rejected_too_large",
                phase="reading upload payload",
                session_id=session_id,
                filename=file.filename or "upload.bin",
                size_bytes=len(payload),
                max_upload_size_bytes=settings.max_upload_size_bytes,
                username=username,
            ),
        )
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
    try:
        upload_hooks = settings.to_runtime_config().upload_hooks
    except ImportError:
        upload_hooks = ()
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
        ),
        hooks=upload_hooks,
    )
    if hook_extra:
        record.extra = {**(record.extra or {}), **hook_extra}
        db.add(record)
    db.commit()
    db.refresh(record)
    logger.info(
        "%s",
        format_log_message(
            "upload stored successfully",
            event="upload.created",
            phase="persisting upload",
            upload_id=record.id,
            session_id=session_id,
            message_id=message_id or "",
            filename=record.filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            storage_key=record.storage_key,
            hook_extra_keys=tuple(sorted(hook_extra.keys())) if hook_extra else (),
            username=username,
        ),
    )
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
        logger.warning(
            "%s",
            format_log_message(
                "upload content is missing on disk",
                event="upload.content_missing",
                phase="resolving upload content",
                upload_id=upload_id,
                session_id=record.session_id,
                storage_key=record.storage_key,
                username=username,
            ),
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload content missing")
    logger.info(
        "%s",
        format_log_message(
            "upload content downloaded",
            event="upload.downloaded",
            phase="serving upload content",
            upload_id=upload_id,
            session_id=record.session_id,
            filename=record.filename,
            size_bytes=record.size_bytes,
            username=username,
        ),
    )
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
        logger.warning(
            "%s",
            format_log_message(
                "upload delete rejected because the upload is already attached to a sent message",
                event="upload.delete_rejected_sent",
                phase="deleting upload",
                upload_id=upload_id,
                session_id=record.session_id,
                message_id=record.message_id,
                username=username,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sent uploads cannot be deleted",
        )

    deleted_storage_key = record.storage_key
    deleted_filename = record.filename
    deleted_session_id = record.session_id
    cleanup_pending_upload_state(db=db, record=record)
    storage.delete(record.storage_key)
    db.delete(record)
    db.commit()
    logger.info(
        "%s",
        format_log_message(
            "pending upload deleted",
            event="upload.deleted",
            phase="deleting upload",
            upload_id=upload_id,
            session_id=deleted_session_id,
            filename=deleted_filename,
            storage_key=deleted_storage_key,
            username=username,
        ),
    )


def cleanup_pending_upload_state(*, db: Session, record: UploadRecord) -> None:
    for cleanup in PENDING_UPLOAD_CLEANUPS:
        cleanup(db, record)
