from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MinioStoragePlaceholder:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str

    def save_bytes(self, **_kwargs: Any) -> tuple[str, str]:
        raise NotImplementedError(
            "MinIO/S3 storage is a planned adapter placeholder in v1. "
            "Swap this in when you provide a real object storage implementation."
        )
