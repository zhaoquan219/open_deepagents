from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConsumePolicy = Literal["once", "keep"]
ConsumeOutcome = Literal["consumed", "pending", "not_ready", "expired"]


class SessionStatePut(BaseModel):
    status: str = Field(default="ready", min_length=1, max_length=32)
    consume_policy: ConsumePolicy = "once"
    value: Any = None
    expires_at: datetime | None = None


class SessionStatePatch(BaseModel):
    status: str | None = Field(default=None, min_length=1, max_length=32)
    consume_policy: ConsumePolicy | None = None
    value: dict[str, Any] | None = None
    expires_at: datetime | None = None


class SessionStateConsumeRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=255)


class SessionStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    namespace: str
    key: str
    status: str
    consume_policy: ConsumePolicy
    value: Any = None
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_consumed_at: datetime | None
    last_consumed_run_id: str | None


class SessionStateConsumeResponse(BaseModel):
    outcome: ConsumeOutcome
    state: SessionStateRead
