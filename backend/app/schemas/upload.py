from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UploadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    message_id: str | None
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    sha256: str
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
