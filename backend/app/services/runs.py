from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.db.models import MessageRecord, SessionRecord
from deepagents_integration import DeepAgentsRuntimeConfig, SseEventEnvelope, stream_sse_envelopes

RunBuilder = Callable[[DeepAgentsRuntimeConfig], Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    return f"run-{uuid4()}"


@dataclass
class RunState:
    run_id: str
    session_id: str
    status: str = "queued"
    created_at: datetime = field(default_factory=utc_now)
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    completed: bool = False

    def publish(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)
        if envelope.get("type") == "status":
            self.status = str(envelope.get("status") or self.status)
        elif envelope.get("type") == "error":
            self.status = "failed"
        for subscriber in list(self.subscribers):
            subscriber.put_nowait(envelope)

    def finish(self, status: str) -> None:
        self.status = status
        self.completed = True
        for subscriber in list(self.subscribers):
            subscriber.put_nowait(None)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

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

        subscriber: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        state.subscribers.add(subscriber)
        try:
            for envelope in _backlog_after(state.envelopes, last_event_id):
                yield _to_sse(envelope)

            while True:
                if state.completed and subscriber.empty():
                    break
                try:
                    item = await asyncio.wait_for(subscriber.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _to_sse(item)
        finally:
            state.subscribers.discard(subscriber)


class RunService:
    def __init__(self, database: DatabaseState, manager: RunManager, builder: RunBuilder) -> None:
        self.database = database
        self.manager = manager
        self.builder = builder

    def start_run(
        self,
        *,
        settings: Settings,
        session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
    ) -> RunState:
        with self.database.session_factory() as db:
            session = _require_session(db, session_id)
            state = self.manager.create(session_id)
            session.last_run_id = state.run_id
            db.add(
                MessageRecord(
                    session_id=session_id,
                    role="user",
                    content=prompt,
                    run_id=state.run_id,
                    extra={"attachments": attachments},
                )
            )
            db.add(session)
            db.commit()

        state.publish(
            _ui_envelope(
                run_id=state.run_id,
                session_id=session_id,
                sequence=1,
                event_type="status",
                status="running",
                label="Run started",
                detail="Queued DeepAgents run.",
                data={"attachments": attachments, "prompt": prompt},
            )
        )
        asyncio.create_task(
            self._execute_run(
                settings=settings,
                run_id=state.run_id,
                session_id=session_id,
                prompt=prompt,
                attachments=attachments,
            )
        )
        return state

    async def _execute_run(
        self,
        *,
        settings: Settings,
        run_id: str,
        session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        state = self.manager.get(run_id)
        if state is None:
            return

        try:
            runtime_config = settings.to_runtime_config()
            if runtime_config.model is None:
                raise RuntimeError("DEEPAGENTS_MODEL is not configured in backend/.env")

            agent = self.builder(runtime_config)
            prompt_with_attachments = prompt
            if attachments:
                names = ", ".join(
                    str(item.get("name") or item.get("id") or "attachment") for item in attachments
                )
                prompt_with_attachments = f"{prompt}\n\nAttached files: {names}"

            final_message = ""
            saw_message_delta = False
            saw_message_final = False
            pending_completion_event: dict[str, Any] | None = None
            sequence = 1
            async for envelope in stream_sse_envelopes(
                agent,
                {"messages": [{"role": "user", "content": prompt_with_attachments}]},
                bridge_run_id=run_id,
            ):
                sequence += 1
                ui_event = _bridge_to_ui(
                    envelope=envelope,
                    session_id=session_id,
                    sequence=sequence,
                )
                if ui_event["type"] == "status" and ui_event["status"] == "completed":
                    pending_completion_event = ui_event
                else:
                    state.publish(ui_event)
                if ui_event["type"] == "message.delta" and ui_event.get("delta"):
                    saw_message_delta = True
                if ui_event["type"] == "message.final":
                    saw_message_final = True
                    final_message = str(ui_event["message"]["content"])
                elif candidate := _extract_message_text(ui_event.get("data")):
                    final_message = candidate

            if final_message and not saw_message_delta:
                sequence += 1
                state.publish(
                    _ui_envelope(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence,
                        event_type="message.delta",
                        status="running",
                        label="message.delta",
                        detail="Assistant response received.",
                        data={},
                        delta=final_message,
                    )
                )

            if final_message and not saw_message_final:
                sequence += 1
                state.publish(
                    _ui_envelope(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence,
                        event_type="message.final",
                        status="completed",
                        label="message.final",
                        detail="Assistant response completed.",
                        data={},
                        message={
                            "id": f"final:{run_id}",
                            "role": "assistant",
                            "content": final_message,
                            "createdAt": utc_now().isoformat(),
                            "attachments": [],
                        },
                    )
                )

            with self.database.session_factory() as db:
                session = _require_session(db, session_id)
                db.add(
                    MessageRecord(
                        session_id=session_id,
                        role="assistant",
                        content=final_message,
                        run_id=run_id,
                        is_final=True,
                    )
                )
                session.last_run_id = run_id
                db.add(session)
                db.commit()

            if pending_completion_event is not None:
                state.publish(pending_completion_event)
            else:
                state.publish(
                    _ui_envelope(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence + 1,
                        event_type="status",
                        status="completed",
                        label="Run completed",
                        detail="DeepAgents run finished successfully.",
                        data={},
                    )
                )
            state.finish("completed")
        except Exception as exc:
            state.publish(
                _ui_envelope(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=len(state.envelopes) + 1,
                    event_type="error",
                    status="failed",
                    label="Run failed",
                    detail=str(exc),
                    data={"error": str(exc)},
                )
            )
            with self.database.session_factory() as db:
                failed_session: SessionRecord | None = (
                    db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
                )
                if failed_session is not None:
                    failed_session.last_run_id = run_id
                    db.add(
                        MessageRecord(
                            session_id=session_id,
                            role="system",
                            content=f"Run failed: {exc}",
                            run_id=run_id,
                            is_final=True,
                        )
                    )
                    db.add(failed_session)
                    db.commit()
            state.finish("failed")


def _require_session(db: Session, session_id: str) -> SessionRecord:
    session = db.query(SessionRecord).filter(SessionRecord.id == session_id).first()
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    return session


def _backlog_after(
    envelopes: list[dict[str, Any]],
    last_event_id: str | None,
) -> list[dict[str, Any]]:
    if not last_event_id:
        return envelopes
    for index, envelope in enumerate(envelopes):
        if envelope["event_id"] == last_event_id:
            return envelopes[index + 1 :]
    return envelopes


def _to_sse(envelope: dict[str, Any]) -> str:
    return f"id: {envelope['event_id']}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"


def _ui_envelope(
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
        "timestamp": utc_now().isoformat(),
        "status": status,
        "step_id": step_id,
        "label": label,
        "detail": detail,
        "delta": delta,
        "message": message,
        "data": data,
    }


def _bridge_to_ui(*, envelope: SseEventEnvelope, session_id: str, sequence: int) -> dict[str, Any]:
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
        status = "completed"
        content = str(data.get("text") or "")
        if not content:
            event_type = "step"
            detail = "Model completed without a direct text payload."
        else:
            message = {
                "id": f"final:{envelope.run_id}",
                "role": "assistant",
                "content": content,
                "createdAt": utc_now().isoformat(),
                "attachments": [],
            }
            detail = "Assistant response completed."
    elif event.startswith("tool."):
        event_type = "tool"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Tool event")
    elif event.startswith("skill."):
        event_type = "skill"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Skill event")
    elif event.startswith("sandbox."):
        event_type = "sandbox"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("name") or "Sandbox event")
    else:
        event_type = "step"
        status = "completed" if event.endswith("completed") else "running"
        detail = str(data.get("node") or data.get("name") or event)

    return _ui_envelope(
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


def _extract_message_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        return "".join(_extract_message_text(item) for item in payload)
    if isinstance(payload, dict):
        if "messages" in payload and isinstance(payload["messages"], list):
            return _extract_message_text(payload["messages"])
        if "output" in payload:
            return _extract_message_text(payload["output"])
        if "content" in payload:
            return _extract_message_text(payload["content"])
        if "text" in payload and isinstance(payload["text"], str):
            return payload["text"]
    return ""
