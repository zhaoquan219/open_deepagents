from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageBase(BaseModel):
    role: str
    content: str
    is_final: bool = True
    run_id: str | None = None
    step_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MessageCreate(MessageBase):
    pass


class MessageUpdate(BaseModel):
    content: str | None = None
    is_final: bool | None = None
    run_id: str | None = None
    step_id: str | None = None
    extra: dict[str, Any] | None = None


class MessageRead(MessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    created_at: datetime
    updated_at: datetime
