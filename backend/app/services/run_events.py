from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.core.config import runtime_now
from app.core.database import DatabaseState
from app.core.session_scope import require_session_record
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
    RunEventViewRecord,
    SessionRuntimeLinkRecord,
)
from deepagents_integration import SseEventEnvelope


def event_now() -> datetime:
    return runtime_now()


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def runtime_config_log_summary(settings: Any) -> tuple[str, dict[str, object]]:
    fields = settings.runtime_model_logging_summary()
    model_name = fields["selected_model_name"]
    if not model_name:
        return "resolved runtime config without a configured model", fields
    provider = fields.get("selected_model_provider") or "configured"
    return f"resolved runtime config using {provider} model {model_name}", fields


def phase_failure_hint(phase: str) -> str:
    return {
        "resolving runtime config": "inspect backend model settings and custom API configuration",
        "building agent": "inspect the DeepAgents builder and runtime dependencies",
        "streaming": "inspect upstream runtime events, tool calls, and model responses",
        "persisting final message": "inspect database writes and final assistant payload",
        "persisting fallback response": "inspect fallback persistence and recursion handling",
        "finalizing completion": "inspect completion event persistence and run state finalization",
    }.get(phase, "inspect the previous stage log and structured metadata for the failing step")


class RunEventViewBuffer:
    def __init__(
        self,
        database: DatabaseState,
        *,
        run_id: str,
        session_id: str,
        batch_size: int = 16,
    ) -> None:
        self.database = database
        self.run_id = run_id
        self.session_id = session_id
        self.batch_size = batch_size
        self.pending: list[dict[str, Any]] = []
        self.persisted_count = 0
        self.skipped_count = 0

    def add(self, envelope: dict[str, Any]) -> None:
        if not should_persist_event_view(envelope):
            self.skipped_count += 1
            return
        self.pending.append(envelope)
        if len(self.pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        envelopes = self.pending
        self.pending = []
        self.persisted_count += len(envelopes)
        persist_event_views(
            self.database,
            run_id=self.run_id,
            session_id=self.session_id,
            envelopes=envelopes,
        )


def backlog_after(
    envelopes: list[dict[str, Any]],
    last_event_id: str | None,
) -> list[dict[str, Any]]:
    if not last_event_id:
        return envelopes
    for index, envelope in enumerate(envelopes):
        if envelope["event_id"] == last_event_id:
            return envelopes[index + 1 :]
    return envelopes


def to_sse(envelope: dict[str, Any]) -> str:
    return f"id: {envelope['event_id']}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"


def should_persist_event_view(envelope: dict[str, Any]) -> bool:
    event_type = str(envelope.get("type") or "")
    raw_data = envelope.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    if data.get("transient") is True:
        return False
    return event_type in {
        "status",
        "message.final",
        "tool",
        "skill",
        "subagent",
        "sandbox",
        "error",
    }


def persist_event_views(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    envelopes: list[dict[str, Any]],
) -> None:
    if not envelopes:
        return

    max_sequence = 0
    latest_status = ""
    records: list[RunEventViewRecord] = []
    for envelope in envelopes:
        event_id = str(envelope["event_id"])
        sequence = int(event_id.rsplit(":", maxsplit=1)[-1])
        max_sequence = max(max_sequence, sequence)
        latest_status = str(envelope.get("status") or latest_status)
        message_payload = envelope.get("message")
        records.append(
            RunEventViewRecord(
                id=event_id,
                run_id=run_id,
                session_id=session_id,
                sequence=sequence,
                event_type=str(envelope.get("type") or ""),
                status=str(envelope.get("status") or "in_progress"),
                message_id=message_payload.get("id")
                if isinstance(message_payload, dict)
                else None,
                step_id=str(envelope.get("step_id") or "") or None,
                payload=dict(envelope),
            )
        )

    with database.session_factory() as db:
        for record in records:
            db.merge(record)
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if run_record is not None:
            current_status = str(run_record.status or "")
            terminal_statuses = {"completed", "failed", "cancelled"}
            if latest_status in terminal_statuses or current_status not in terminal_statuses:
                run_record.status = latest_status or run_record.status
            run_record.event_count = max(run_record.event_count, max_sequence)
            db.add(run_record)
        db.commit()


def ui_envelope(
    *,
    run_id: str,
    session_id: str,
    sequence: int,
    event_type: str,
    status: str,
    label: str,
    detail: str,
    data: dict[str, Any],
    step_id: str = "",
    delta: str = "",
    message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": "deepagents-ui.v1",
        "event_id": f"{run_id}:{sequence:06d}",
        "type": event_type,
        "run_id": run_id,
        "session_id": session_id,
        "timestamp": event_now().isoformat(),
        "status": status,
        "step_id": step_id,
        "label": label,
        "detail": detail,
        "delta": delta,
        "message": message,
        "data": data,
    }


def bridge_to_ui(*, envelope: SseEventEnvelope, session_id: str, sequence: int) -> dict[str, Any]:
    event = envelope.event
    data = envelope.data
    status = "running"
    event_type = "step"
    detail = ""
    delta = ""
    message: dict[str, Any] | None = None

    if event == "run.started":
        event_type = "status"
        detail = "DeepAgents run started."
    elif event == "run.completed":
        event_type = "status"
        status = "completed"
        detail = "DeepAgents run completed."
    elif event == "run.failed":
        event_type = "error"
        status = "failed"
        detail = str(data.get("error") or "DeepAgents run failed.")
    elif event == "message.delta":
        event_type = "message.delta"
        delta = str(data.get("text") or "")
        detail = "Streaming assistant response."
    elif event == "message.completed":
        event_type = "message.final"
        content = extract_message_text(data)
        if not content:
            event_type = "step"
            detail = "Model completed without a direct text payload."
        else:
            data = {**data, "final": False}
            message = {
                "id": f"message:{envelope.event_id}",
                "role": "assistant",
                "content": content,
                "createdAt": event_now().isoformat(),
                "attachments": [],
            }
            detail = "Assistant response updated."
    elif event.startswith("tool."):
        event_type = "tool"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Tool event")
    elif event.startswith("skill."):
        event_type = "skill"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Skill event")
    elif event.startswith("subagent."):
        event_type = "subagent"
        status = "completed" if event.endswith("completed") else "running"
        detail = _subagent_event_detail(data)
    elif event.startswith("sandbox."):
        event_type = "sandbox"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Sandbox event")
    else:
        event_type = "step"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("node") or data.get("name") or event)

    return ui_envelope(
        run_id=envelope.run_id,
        session_id=session_id,
        sequence=sequence,
        event_type=event_type,
        status=status,
        label=event,
        detail=detail,
        data=data,
        step_id=str(data.get("step_id") or ""),
        delta=delta,
        message=message,
    )


def extract_message_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return extract_message_text(payload[-1]) if payload else ""
    if isinstance(payload, dict):
        if "messages" in payload and isinstance(payload["messages"], list) and payload["messages"]:
            return extract_message_text(payload["messages"][-1])
        if "output" in payload:
            return extract_message_text(payload["output"])
        if "content" in payload:
            return extract_message_text(payload["content"])
        if "text" in payload and isinstance(payload["text"], str):
            return payload["text"]
    return ""


def restore_subagent_detail(
    ui_event: dict[str, Any],
    *,
    subagent_names_by_runtime_run: dict[str, str],
) -> None:
    raw_data = ui_event.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    runtime_run_id = str(data.get("runtime_run_id") or ui_event.get("run_id") or "")
    detail = str(ui_event.get("detail") or "")
    if ui_event.get("status") == "running":
        if detail and detail not in {"task", "Subagent event"}:
            subagent_names_by_runtime_run[runtime_run_id] = detail
        return
    if detail not in {"task", "Subagent event"}:
        return
    restored = subagent_names_by_runtime_run.get(runtime_run_id)
    if not restored:
        return
    ui_event["detail"] = restored
    ui_event["data"] = {**data, "subagent_type": restored}


def is_runtime_placeholder_text(value: str) -> bool:
    text = value.strip()
    return text.startswith("[omitted long runtime string:") or text.startswith(
        "[redacted base64-like runtime string:"
    )


def count_runtime_events(counter: Counter[str], prefix: str) -> int:
    return sum(count for name, count in counter.items() if name.startswith(prefix))


MAX_REPLAY_BACKLOG_EVENTS = 500


def new_run_id() -> str:
    return f"run-{uuid4()}"


@dataclass(frozen=True)
class RunSubscriber:
    queue: asyncio.Queue[dict[str, Any] | None]
    loop: asyncio.AbstractEventLoop


@dataclass
class RunState:
    run_id: str
    session_id: str
    status: str = "queued"
    created_at: datetime = field(default_factory=event_now)
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[RunSubscriber] = field(default_factory=set)
    completed: bool = False
    cancel_requested: bool = False
    execution_loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    execution_task: asyncio.Task[None] | None = field(default=None, repr=False)
    last_sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, envelope: dict[str, Any]) -> bool:
        with self.lock:
            if self.completed:
                return False
            self._remember_sequence(envelope)
            self._append_replay_envelope(envelope)
            if envelope.get("type") == "status":
                self.status = str(envelope.get("status") or self.status)
            elif envelope.get("type") == "error":
                self.status = "failed"
            subscribers = tuple(self.subscribers)
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, envelope)
        return True

    def finish(self, status: str) -> None:
        self.terminalize(status)

    def terminalize(self, status: str, envelope: dict[str, Any] | None = None) -> bool:
        with self.lock:
            if self.completed:
                return False
            if envelope is not None:
                self._remember_sequence(envelope)
                self._append_replay_envelope(envelope)
            self.status = status
            self.completed = True
            subscribers = tuple(self.subscribers)
        if envelope is not None:
            for subscriber in subscribers:
                subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, envelope)
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, None)
        return True

    def backlog_after(self, last_event_id: str | None) -> list[dict[str, Any]]:
        with self.lock:
            return backlog_after(list(self.envelopes), last_event_id)

    def _append_replay_envelope(self, envelope: dict[str, Any]) -> None:
        data = envelope.get("data")
        if isinstance(data, dict) and data.get("transient") is True:
            return
        self.envelopes.append(envelope)
        if len(self.envelopes) > MAX_REPLAY_BACKLOG_EVENTS:
            self.envelopes = self.envelopes[-MAX_REPLAY_BACKLOG_EVENTS:]

    def _remember_sequence(self, envelope: dict[str, Any]) -> None:
        try:
            sequence = int(str(envelope.get("event_id") or "").rsplit(":", maxsplit=1)[-1])
        except ValueError:
            return
        self.last_sequence = max(self.last_sequence, sequence)

    def add_subscriber(self, subscriber: RunSubscriber) -> None:
        with self.lock:
            self.subscribers.add(subscriber)
            completed = self.completed
        if completed:
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, None)

    def discard_subscriber(self, subscriber: RunSubscriber) -> None:
        with self.lock:
            self.subscribers.discard(subscriber)

    def bind_execution(
        self,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[None],
    ) -> None:
        with self.lock:
            self.execution_loop = loop
            self.execution_task = task
            should_cancel = self.cancel_requested or self.completed
        if should_cancel:
            loop.call_soon_threadsafe(task.cancel)

    def clear_execution(self, task: asyncio.Task[None]) -> None:
        with self.lock:
            if self.execution_task is task:
                self.execution_task = None
                self.execution_loop = None

    def request_cancel(self) -> bool:
        with self.lock:
            if self.completed:
                return False
            self.cancel_requested = True
            loop = self.execution_loop
            task = self.execution_task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        return True

    def next_sequence(self) -> int:
        with self.lock:
            return self.last_sequence + 1


class RunManager:
    def __init__(self, keepalive_interval: float = 15.0) -> None:
        self._runs: dict[str, RunState] = {}
        self.keepalive_interval = keepalive_interval

    def create(self, session_id: str) -> RunState:
        state = RunState(run_id=new_run_id(), session_id=session_id)
        self._runs[state.run_id] = state
        return state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    async def stream(self, run_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        state = self.get(run_id)
        if state is None:
            raise KeyError(run_id)

        subscriber = RunSubscriber(queue=asyncio.Queue(), loop=asyncio.get_running_loop())
        state.add_subscriber(subscriber)
        try:
            for envelope in state.backlog_after(last_event_id):
                yield to_sse(envelope)

            while True:
                try:
                    item = await asyncio.wait_for(
                        subscriber.queue.get(),
                        timeout=self.keepalive_interval,
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield to_sse(item)
        finally:
            state.discard_subscriber(subscriber)


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


def persist_single_event(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    envelope: dict[str, Any],
) -> None:
    persist_event_views(
        database,
        run_id=run_id,
        session_id=session_id,
        envelopes=[envelope],
    )


def persist_runtime_link(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    runtime_run_id: str,
    runtime_thread_id: str,
) -> bool:
    if not runtime_run_id:
        return False
    with database.session_factory() as db:
        record = (
            db.query(SessionRuntimeLinkRecord)
            .filter(SessionRuntimeLinkRecord.session_id == session_id)
            .first()
        )
        if record is None:
            record = SessionRuntimeLinkRecord(session_id=session_id)
        record.runtime_thread_id = runtime_thread_id
        record.runtime_run_id = runtime_run_id
        record.last_seen_at = event_now()
        db.add(record)
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if run_record is None:
            db.commit()
            return False
        run_record.runtime_run_id = runtime_run_id
        db.add(run_record)
        db.commit()
        return True


def create_message_record(
    database: DatabaseState,
    *,
    session_id: str,
    role: str,
    content: str,
    run_id: str | None = None,
    is_final: bool = True,
    step_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    with database.session_factory() as db:
        session = require_session_record(db, session_id=session_id)
        record = MessageRecord(
            session_id=session_id,
            role=role,
            content=content,
            run_id=run_id,
            is_final=is_final,
            step_id=step_id,
            extra=extra or {},
        )
        session.last_run_id = run_id or session.last_run_id
        db.add(record)
        db.add(session)
        db.commit()
        db.refresh(record)
        return str(record.id)


def persist_recursion_fallback(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    state: RunState,
    fallback_message: str,
    warning: str,
) -> None:
    fallback_event = ui_envelope(
        run_id=run_id,
        session_id=session_id,
        sequence=len(state.envelopes) + 1,
        event_type="message.final",
        status="completed",
        label="message.final",
        detail="Assistant response completed with fallback after recursion limit.",
        data={"warning": warning},
        message={
            "id": f"final:{run_id}",
            "role": "assistant",
            "content": fallback_message,
                "createdAt": event_now().isoformat(),
            "attachments": [],
        },
    )
    persist_single_event(database, run_id=run_id, session_id=session_id, envelope=fallback_event)
    state.publish(fallback_event)
    completion_event = ui_envelope(
        run_id=run_id,
        session_id=session_id,
        sequence=len(state.envelopes) + 1,
        event_type="status",
        status="completed",
        label="Run completed",
        detail="DeepAgents run completed with a fallback response.",
        data={"warning": warning},
    )
    persist_single_event(database, run_id=run_id, session_id=session_id, envelope=completion_event)
    state.publish(completion_event)
    with database.session_factory() as db:
        session = require_session_record(db, session_id=session_id)
        db.add(
            MessageRecord(
                session_id=session_id,
                role="assistant",
                content=fallback_message,
                run_id=run_id,
                is_final=True,
            )
        )
        session.last_run_id = run_id
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if run_record is not None:
            run_record.status = "completed"
            run_record.error_text = warning
            run_record.final_output_text = fallback_message
            run_record.event_count = len(state.envelopes)
            run_record.completed_at = event_now()
            db.add(run_record)
        db.add(session)
        db.commit()


def persist_failed_run(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    state: RunState,
    error_text: str,
) -> None:
    error_envelope = ui_envelope(
        run_id=run_id,
        session_id=session_id,
        sequence=len(state.envelopes) + 1,
        event_type="error",
        status="failed",
        label="Run failed",
        detail=error_text,
        data={"error": error_text},
    )
    persist_single_event(database, run_id=run_id, session_id=session_id, envelope=error_envelope)
    state.publish(error_envelope)
    with database.session_factory() as db:
        failed_session = require_session_record(db, session_id=session_id)
        failed_session.last_run_id = run_id
        db.add(
            MessageRecord(
                session_id=session_id,
                role="system",
                content=f"Run failed: {error_text}",
                run_id=run_id,
                is_final=True,
            )
        )
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if run_record is not None:
            run_record.status = "failed"
            run_record.error_text = error_text
            run_record.event_count = len(state.envelopes)
            run_record.completed_at = event_now()
            db.add(run_record)
        db.add(failed_session)
        db.commit()


def finalize_cancelled_run(
    database: DatabaseState,
    *,
    run_id: str,
    session_id: str,
    detail: str,
    state: RunState | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    current_state = state
    if current_state is not None and current_state.status == "cancelled":
        current_state.request_cancel()
        return False, None
    with database.session_factory() as db:
        run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if run_record is None:
            raise KeyError(run_id)
        if run_record.status in {"completed", "failed", "cancelled"}:
            if current_state is not None:
                current_state.request_cancel()
            return False, None
        sequence = (
            current_state.next_sequence()
            if current_state is not None
            else run_record.event_count + 1
        )
        cancel_envelope = ui_envelope(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            event_type="status",
            status="cancelled",
            label="Run cancelled",
            detail=detail,
            data={"cancelled_by": "user"},
        )
        db.merge(
            RunEventViewRecord(
                id=str(cancel_envelope["event_id"]),
                run_id=run_id,
                session_id=session_id,
                sequence=sequence,
                event_type="status",
                status="cancelled",
                step_id=None,
                message_id=None,
                payload=dict(cancel_envelope),
            )
        )
        run_record.status = "cancelled"
        run_record.error_text = detail
        run_record.event_count = max(run_record.event_count, sequence)
        run_record.completed_at = event_now()
        db.add(run_record)
        session = require_session_record(db, session_id=session_id)
        session.last_run_id = run_id
        db.add(session)
        db.commit()
    if current_state is not None:
        current_state.request_cancel()
        return current_state.terminalize("cancelled", cancel_envelope), cancel_envelope
    return True, cancel_envelope


def _subagent_event_detail(data: dict[str, Any]) -> str:
    raw_input = data.get("input")
    input_data = raw_input if isinstance(raw_input, dict) else {}
    subagent_type = str(
        input_data.get("subagent_type")
        or input_data.get("subagent")
        or input_data.get("agent")
        or data.get("subagent_type")
        or ""
    ).strip()
    return subagent_type or str(data.get("name") or "Subagent event")
