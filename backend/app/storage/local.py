from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4


_FILENAME_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def save_bytes(self, *, session_id: str, filename: str, payload: bytes) -> tuple[str, str]:
        safe_name = _safe_filename(filename)
        token = uuid4().hex[:12]
        relative_path = Path(f"{token}-{safe_name}")
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
