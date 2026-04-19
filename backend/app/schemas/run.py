from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    session_id: str
    prompt: str = Field(min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    model_id: str | None = None
    subagent_profile_id: str | None = None


class RunRead(BaseModel):
    run_id: str
    session_id: str
    status: str
    created_at: datetime
