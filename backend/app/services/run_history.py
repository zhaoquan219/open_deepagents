from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from app.core.database import DatabaseState
from app.db.models import AgentRunRecord, RunEventViewRecord

from .run_events import backlog_after, to_sse


@dataclass(frozen=True)
class PersistedRunState:
    run_id: str
    session_id: str
    status: str
    created_at: datetime


def load_persisted_run_state(
    database: DatabaseState,
    *,
    run_id: str,
) -> PersistedRunState | None:
    with database.session_factory() as db:
        record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if record is None:
            return None
        return PersistedRunState(
            run_id=record.id,
            session_id=record.session_id,
            status=record.status,
            created_at=record.created_at,
        )


async def stream_persisted_run(
    database: DatabaseState,
    *,
    run_id: str,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    with database.session_factory() as db:
        records = (
            db.query(RunEventViewRecord)
            .filter(RunEventViewRecord.run_id == run_id)
            .order_by(RunEventViewRecord.sequence.asc())
            .all()
        )
    if not records:
        raise KeyError(run_id)
    envelopes = [dict(record.payload or {}) for record in records]
    for envelope in backlog_after(envelopes, last_event_id):
        yield to_sse(envelope)
