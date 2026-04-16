from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4


_FILENAME_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_SESSION_TOKEN_LENGTH = 8
_UPLOAD_TOKEN_LENGTH = 10


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def save_bytes(self, *, session_id: str, filename: str, payload: bytes) -> tuple[str, str]:
        safe_name = _safe_filename(filename)
        session_token = _short_session_token(session_id)
        upload_token = uuid4().hex[:_UPLOAD_TOKEN_LENGTH]
        relative_path = Path(session_token) / f"{upload_token}-{safe_name}"
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return relative_path.as_posix(), digest

    def resolve(self, storage_key: str) -> Path:
        return self.root / storage_key

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)


def _safe_filename(filename: str) -> str:
    raw_name = Path(filename).name or "upload.bin"
    normalized = _FILENAME_SAFE_CHARS.sub("-", raw_name).strip(".-_")
    if not normalized:
        return "upload.bin"
    return normalized[:80]


def _short_session_token(session_id: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", session_id)
    if compact:
        return compact[:_SESSION_TOKEN_LENGTH]
    return uuid4().hex[:_SESSION_TOKEN_LENGTH]
