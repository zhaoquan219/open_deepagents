from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.message import MessageRead
from app.schemas.upload import UploadRead


class SessionCreate(BaseModel):
    title: str = "New session"
    runtime_thread_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    runtime_thread_id: str | None = None
    last_run_id: str | None = None
    extra: dict[str, Any] | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    runtime_thread_id: str | None
    last_run_id: str | None
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionRead):
    messages: list[MessageRead]
    uploads: list[UploadRead]
