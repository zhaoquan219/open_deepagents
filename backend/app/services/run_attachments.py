from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.db.models import MessageRecord, UploadRecord
from app.storage import LocalStorage


class InvalidRunAttachmentError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def resolve_run_attachments(
    *,
    db: Session,
    session_id: str,
    attachments: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    if not attachments:
        return []

    requested_ids = [str(item.get("id") or "").strip() for item in attachments if item.get("id")]
    records_by_id = {}
    if requested_ids:
        records = (
            db.query(UploadRecord)
            .filter(
                UploadRecord.session_id == session_id,
                UploadRecord.id.in_(requested_ids),
            )
            .all()
        )
        records_by_id = {record.id: record for record in records}
    requested_storage_keys = [
        str(item.get("storage_key") or "").strip()
        for item in attachments
        if not item.get("id") and item.get("storage_key")
    ]
    records_by_storage_key = {}
    if requested_storage_keys:
        records = (
            db.query(UploadRecord)
            .filter(
                UploadRecord.session_id == session_id,
                UploadRecord.storage_key.in_(requested_storage_keys),
            )
            .all()
        )
        records_by_storage_key = {record.storage_key: record for record in records}

    resolved: list[dict[str, Any]] = []
    for item in attachments:
        attachment_id = str(item.get("id") or "").strip()
        record = records_by_id.get(attachment_id)
        if attachment_id and record is None:
            raise InvalidRunAttachmentError("Invalid attachment")
        storage_key = str(item.get("storage_key") or "").strip()
        if record is None and storage_key:
            record = records_by_storage_key.get(storage_key)
            if record is None:
                raise InvalidRunAttachmentError("Invalid attachment")
        if record is not None:
            if record.message_id is not None:
                raise InvalidRunAttachmentError(
                    "Upload is already attached to a message",
                    status_code=409,
                )
            upload_path = resolve_attachment_disk_path(
                upload_root=settings.upload_storage_dir,
                storage_key=record.storage_key,
            )
            resolved.append(
                {
                    "id": record.id,
                    "name": record.filename,
                    "status": str(item.get("status") or "uploaded"),
                    "size": record.size_bytes,
                    "size_bytes": record.size_bytes,
                    "content_type": record.content_type,
                    "storage_key": record.storage_key,
                    "upload_path": str(upload_path) if upload_path is not None else "",
                    "sandbox_path": resolve_sandbox_attachment_path(
                        upload_path=upload_path,
                        settings=settings,
                    ),
                }
            )
            continue

        upload_path = resolve_attachment_disk_path(
            upload_root=settings.upload_storage_dir,
            storage_key=storage_key,
        )
        resolved.append(
            {
                "id": attachment_id or str(item.get("attachment_id") or ""),
                "name": str(item.get("name") or item.get("filename") or "attachment"),
                "status": str(item.get("status") or "uploaded"),
                "size": int(item.get("size") or item.get("size_bytes") or 0),
                "size_bytes": int(item.get("size_bytes") or item.get("size") or 0),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "storage_key": storage_key,
                "upload_path": str(upload_path) if upload_path is not None else "",
                "sandbox_path": resolve_sandbox_attachment_path(
                    upload_path=upload_path,
                    settings=settings,
                ),
            }
        )

    return resolved


def pending_upload_records_for_attachments(
    *,
    db: Session,
    session_id: str,
    attachments: list[dict[str, Any]],
) -> list[UploadRecord]:
    upload_ids = [str(item.get("id") or "").strip() for item in attachments if item.get("id")]
    if not upload_ids:
        return []

    records = (
        db.query(UploadRecord)
        .filter(
            UploadRecord.session_id == session_id,
            UploadRecord.id.in_(upload_ids),
        )
        .all()
    )
    records_by_id = {record.id: record for record in records}

    ordered_records: list[UploadRecord] = []
    for upload_id in upload_ids:
        record = records_by_id.get(upload_id)
        if record is None:
            raise InvalidRunAttachmentError("Invalid attachment")
        if record.message_id is not None:
            raise InvalidRunAttachmentError(
                "Upload is already attached to a message",
                status_code=409,
            )
        ordered_records.append(record)
    return ordered_records


def persist_generated_state_outputs(
    *,
    database: DatabaseState,
    settings: Settings,
    session_id: str,
    run_id: str,
    files: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    storage = LocalStorage(settings.upload_storage_dir)
    attachments: list[dict[str, Any]] = []
    with database.session_factory() as db:
        for runtime_path, file_data in sorted(files.items()):
            payload = state_file_payload_bytes(file_data)
            filename = state_output_filename(runtime_path)
            storage_key, digest = storage.save_bytes(
                session_id=session_id,
                filename=filename,
                payload=payload,
            )
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            record = UploadRecord(
                session_id=session_id,
                message_id=None,
                filename=filename,
                content_type=content_type,
                size_bytes=len(payload),
                storage_key=storage_key,
                sha256=digest,
                extra={
                    "source": "state_output",
                    "runtime_path": runtime_path,
                    "run_id": run_id,
                },
            )
            db.add(record)
            db.flush()
            attachments.append(upload_attachment_manifest(record))
        db.commit()
    return attachments


def resolve_attachment_disk_path(*, upload_root: Path, storage_key: str) -> Path | None:
    if not storage_key:
        return None
    return (upload_root / storage_key).resolve()


def state_backend_files(*, attachments: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    files: dict[str, dict[str, str]] = {}
    for attachment in attachments:
        sandbox_path = str(attachment.get("sandbox_path") or "").strip()
        upload_path = str(attachment.get("upload_path") or "").strip()
        if not sandbox_path or not upload_path:
            continue
        disk_path = Path(upload_path)
        if not disk_path.is_file():
            continue
        payload = disk_path.read_bytes()
        try:
            files[sandbox_path] = {
                "content": payload.decode("utf-8"),
                "encoding": "utf-8",
            }
        except UnicodeDecodeError:
            files[sandbox_path] = {
                "content": base64.b64encode(payload).decode("ascii"),
                "encoding": "base64",
            }
    return files


def generated_state_output_files(
    completion_output: Any,
    *,
    initial_files: Any,
) -> dict[str, dict[str, Any]]:
    output = (
        completion_output.get("output")
        if isinstance(completion_output, dict) and "output" in completion_output
        else completion_output
    )
    if not isinstance(output, dict):
        return {}
    raw_files = output.get("files")
    if not isinstance(raw_files, dict):
        return {}
    initial = initial_files if isinstance(initial_files, dict) else {}
    generated: dict[str, dict[str, Any]] = {}
    for path, file_data in raw_files.items():
        runtime_path = str(path or "").strip()
        if not runtime_path or not isinstance(file_data, dict):
            continue
        if runtime_path in initial and state_file_payload_equal(file_data, initial[runtime_path]):
            continue
        generated[runtime_path] = file_data
    return generated


def state_file_payload_equal(left: dict[str, Any], right: Any) -> bool:
    if not isinstance(right, dict):
        return False
    return (
        str(left.get("encoding") or "utf-8") == str(right.get("encoding") or "utf-8")
        and state_file_content_string(left) == state_file_content_string(right)
    )


def state_file_payload_bytes(file_data: dict[str, Any]) -> bytes:
    content = state_file_content_string(file_data)
    encoding = str(file_data.get("encoding") or "utf-8")
    if encoding == "base64":
        try:
            return base64.standard_b64decode(content)
        except (binascii.Error, ValueError):
            return content.encode("utf-8")
    return content.encode("utf-8")


def state_file_content_string(file_data: dict[str, Any]) -> str:
    content = file_data.get("content", "")
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def state_output_filename(runtime_path: str) -> str:
    name = Path(runtime_path).name.strip()
    return name or "state-output.txt"


def upload_attachment_manifest(record: UploadRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.filename,
        "filename": record.filename,
        "size": record.size_bytes,
        "size_bytes": record.size_bytes,
        "status": "generated",
        "content_type": record.content_type,
        "storage_key": record.storage_key,
        "source": "state_output",
        "runtime_path": (record.extra or {}).get("runtime_path", ""),
    }


def append_message_attachments(record: MessageRecord, attachments: list[dict[str, Any]]) -> None:
    if not attachments:
        return
    extra = dict(record.extra or {})
    existing = extra.get("attachments")
    extra["attachments"] = [
        *(existing if isinstance(existing, list) else []),
        *attachments,
    ]
    record.extra = extra


def link_uploads_to_message(
    db: Session,
    *,
    attachments: list[dict[str, Any]],
    message_id: str,
) -> None:
    if not attachments:
        return
    upload_ids = [str(item.get("id") or "") for item in attachments if item.get("id")]
    if not upload_ids:
        return
    for record in db.query(UploadRecord).filter(UploadRecord.id.in_(upload_ids)).all():
        record.message_id = message_id
        db.add(record)


def resolve_sandbox_attachment_path(*, upload_path: Path | None, settings: Settings) -> str:
    if upload_path is None:
        return ""

    if settings.deepagents_sandbox_kind == "state":
        return state_attachment_path(upload_path=upload_path, settings=settings)

    sandbox_root = resolved_sandbox_root(settings)
    if sandbox_root is None:
        return ""

    try:
        relative_path = upload_path.resolve().relative_to(sandbox_root)
    except ValueError:
        return ""

    normalized = relative_path.as_posix()
    if settings.resolved_sandbox_virtual_mode():
        return f"/{normalized.lstrip('/')}"
    return normalized


def resolved_sandbox_root(settings: Settings) -> Path | None:
    root_dir = settings.resolved_sandbox_root_dir()
    if root_dir is not None:
        return Path(root_dir).expanduser().resolve()
    return None


def state_attachment_path(*, upload_path: Path, settings: Settings) -> str:
    try:
        relative_path = upload_path.resolve().relative_to(settings.upload_storage_dir.resolve())
    except ValueError:
        return f"/uploads/{upload_path.name}"
    return f"/uploads/{relative_path.as_posix().lstrip('/')}"


def message_attachments(*, record: MessageRecord) -> list[dict[str, Any]]:
    extra = record.extra if isinstance(record.extra, dict) else {}
    attachments = extra.get("attachments")
    if isinstance(attachments, list):
        return [item for item in attachments if isinstance(item, dict)]
    return []
