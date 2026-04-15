from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def save_bytes(self, *, session_id: str, filename: str, payload: bytes) -> tuple[str, str]:
        safe_name = Path(filename).name or "upload.bin"
        relative_path = Path(session_id) / f"{uuid4()}-{safe_name}"
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return relative_path.as_posix(), digest

    def resolve(self, storage_key: str) -> Path:
        return self.root / storage_key

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)
