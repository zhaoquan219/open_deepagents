from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
    RunEventViewRecord,
    SessionRecord,
    SessionRuntimeLinkRecord,
)
from deepagents_integration import DeepAgentsRuntimeConfig, SseEventEnvelope, stream_sse_envelopes

RunBuilder = Callable[[DeepAgentsRuntimeConfig], Any]


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    created_at: datetime = field(default_factory=utc_now)
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[RunSubscriber] = field(default_factory=set)
    completed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, envelope: dict[str, Any]) -> None:
        with self.lock:
            self.envelopes.append(envelope)
            if envelope.get("type") == "status":
                self.status = str(envelope.get("status") or self.status)
            elif envelope.get("type") == "error":
                self.status = "failed"
            subscribers = tuple(self.subscribers)
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, envelope)

    def finish(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.completed = True
            subscribers = tuple(self.subscribers)
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, None)

    def backlog_after(self, last_event_id: str | None) -> list[dict[str, Any]]:
        with self.lock:
            return _backlog_after(list(self.envelopes), last_event_id)

    def add_subscriber(self, subscriber: RunSubscriber) -> None:
        with self.lock:
            self.subscribers.add(subscriber)

    def discard_subscriber(self, subscriber: RunSubscriber) -> None:
        with self.lock:
            self.subscribers.discard(subscriber)

    def is_drained(self, subscriber: RunSubscriber) -> bool:
        with self.lock:
            return self.completed and subscriber.queue.empty()


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
                yield _to_sse(envelope)

            while True:
                if state.is_drained(subscriber):
                    break
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
                yield _to_sse(item)
        finally:
            state.discard_subscriber(subscriber)


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
                AgentRunRecord(
                    id=state.run_id,
                    session_id=session_id,
                    status="queued",
                    prompt=prompt,
                    extra={"attachments": attachments},
                )
            )
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
        self._launch_run(
            settings=settings,
            run_id=state.run_id,
            session_id=session_id,
            prompt=prompt,
            attachments=attachments,
        )
        return state

    def _launch_run(
        self,
        *,
        settings: Settings,
        run_id: str,
        session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        def runner() -> None:
            asyncio.run(
                self._execute_run(
                    settings=settings,
                    run_id=run_id,
                    session_id=session_id,
                    prompt=prompt,
                    attachments=attachments,
                )
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            return
        loop.run_in_executor(None, runner)

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
            agent_input = self._build_agent_input(
                session_id=session_id,
                run_id=run_id,
                prompt=prompt,
                attachments=attachments,
            )

            final_message = ""
            saw_message_delta = False
            saw_message_final = False
            pending_completion_event: dict[str, Any] | None = None
            sequence = 1
            async for envelope in stream_sse_envelopes(
                agent,
                agent_input,
                bridge_run_id=run_id,
                config={"recursion_limit": settings.deepagents_recursion_limit},
            ):
                sequence += 1
                ui_event = _bridge_to_ui(
                    envelope=envelope,
                    session_id=session_id,
                    sequence=sequence,
                )
                self._persist_event(run_id=run_id, session_id=session_id, envelope=ui_event)
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
                self._sync_runtime_link(
                    session_id=session_id,
                    runtime_run_id=str(ui_event.get("data", {}).get("runtime_run_id") or ""),
                )

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
                run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
                if run_record is not None:
                    run_record.status = "completed"
                    run_record.final_output_text = final_message
                    run_record.event_count = len(state.envelopes)
                    run_record.completed_at = utc_now()
                    db.add(run_record)
                db.add(session)
                db.commit()

            if pending_completion_event is not None:
                self._persist_event(
                    run_id=run_id,
                    session_id=session_id,
                    envelope=pending_completion_event,
                )
                state.publish(pending_completion_event)
            else:
                completion_envelope = _ui_envelope(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence + 1,
                        event_type="status",
                        status="completed",
                        label="Run completed",
                        detail="DeepAgents run finished successfully.",
                        data={},
                    )
                self._persist_event(
                    run_id=run_id,
                    session_id=session_id,
                    envelope=completion_envelope,
                )
                state.publish(completion_envelope)
            state.finish("completed")
        except GraphRecursionError as exc:
            fallback_message = _build_recursion_fallback(prompt, agent_input["messages"])
            fallback_event = _ui_envelope(
                run_id=run_id,
                session_id=session_id,
                sequence=len(state.envelopes) + 1,
                event_type="message.final",
                status="completed",
                label="message.final",
                detail="Assistant response completed with fallback after recursion limit.",
                data={"warning": str(exc)},
                message={
                    "id": f"final:{run_id}",
                    "role": "assistant",
                    "content": fallback_message,
                    "createdAt": utc_now().isoformat(),
                    "attachments": [],
                },
            )
            self._persist_event(run_id=run_id, session_id=session_id, envelope=fallback_event)
            state.publish(fallback_event)
            completion_event = _ui_envelope(
                run_id=run_id,
                session_id=session_id,
                sequence=len(state.envelopes) + 1,
                event_type="status",
                status="completed",
                label="Run completed",
                detail="DeepAgents run completed with a fallback response.",
                data={"warning": str(exc)},
            )
            self._persist_event(run_id=run_id, session_id=session_id, envelope=completion_event)
            state.publish(completion_event)
            with self.database.session_factory() as db:
                recovered_session = _require_session(db, session_id)
                db.add(
                    MessageRecord(
                        session_id=session_id,
                        role="assistant",
                        content=fallback_message,
                        run_id=run_id,
                        is_final=True,
                    )
                )
                recovered_session.last_run_id = run_id
                run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
                if run_record is not None:
                    run_record.status = "completed"
                    run_record.error_text = str(exc)
                    run_record.final_output_text = fallback_message
                    run_record.event_count = len(state.envelopes)
                    run_record.completed_at = utc_now()
                    db.add(run_record)
                db.add(recovered_session)
                db.commit()
            state.finish("completed")
        except Exception as exc:
            error_envelope = _ui_envelope(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=len(state.envelopes) + 1,
                    event_type="error",
                    status="failed",
                    label="Run failed",
                    detail=str(exc),
                    data={"error": str(exc)},
                )
            self._persist_event(run_id=run_id, session_id=session_id, envelope=error_envelope)
            state.publish(error_envelope)
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
                    run_record = (
                        db.query(AgentRunRecord)
                        .filter(AgentRunRecord.id == run_id)
                        .first()
                    )
                    if run_record is not None:
                        run_record.status = "failed"
                        run_record.error_text = str(exc)
                        run_record.event_count = len(state.envelopes)
                        run_record.completed_at = utc_now()
                        db.add(run_record)
                    db.add(failed_session)
                    db.commit()
            state.finish("failed")

    def _build_agent_input(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, str]]]:
        with self.database.session_factory() as db:
            records = (
                db.query(MessageRecord)
                .filter(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
                .all()
            )

        messages: list[dict[str, str]] = []
        for record in records:
            if record.role not in {"user", "assistant"}:
                continue
            content = record.content or ""
            if record.role == "user" and record.run_id == run_id:
                content = _append_attachment_names(content, attachments)
            if not content.strip():
                continue
            messages.append({"role": record.role, "content": content})

        if messages:
            return {"messages": messages}

        return {
            "messages": [
                {
                    "role": "user",
                    "content": _append_attachment_names(prompt, attachments),
                }
            ]
        }

    def _persist_event(self, *, run_id: str, session_id: str, envelope: dict[str, Any]) -> None:
        event_id = str(envelope["event_id"])
        sequence = int(event_id.rsplit(":", maxsplit=1)[-1])
        with self.database.session_factory() as db:
            message_payload = envelope.get("message")
            record = RunEventViewRecord(
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
            db.merge(record)
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            if run_record is not None:
                run_record.status = str(envelope.get("status") or run_record.status)
                run_record.event_count = max(run_record.event_count, sequence)
                db.add(run_record)
            db.commit()

    def _sync_runtime_link(self, *, session_id: str, runtime_run_id: str) -> None:
        if not runtime_run_id:
            return
        with self.database.session_factory() as db:
            record = (
                db.query(SessionRuntimeLinkRecord)
                .filter(SessionRuntimeLinkRecord.session_id == session_id)
                .first()
            )
            if record is None:
                record = SessionRuntimeLinkRecord(session_id=session_id)
            record.runtime_run_id = runtime_run_id
            record.last_seen_at = utc_now()
            db.add(record)
            db.commit()


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
        content = _extract_message_text(data)
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


def _append_attachment_names(prompt: str, attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return prompt
    names = ", ".join(
        str(item.get("name") or item.get("id") or "attachment") for item in attachments
    )
    return f"{prompt}\n\nAttached files: {names}"


def _build_recursion_fallback(prompt: str, messages: list[dict[str, str]]) -> str:
    if "目录" in prompt:
        if any("没有文件" in str(message.get("content", "")) for message in messages):
            return (
                "我多次尝试用现有工具确认目录，但当前可访问文件视图仍然为空，"
                "暂时无法给出更具体的目录路径。"
            )
        return "我多次尝试用现有工具确认目录，但当前工具结果还不足以给出稳定的目录路径。"
    if "文件" in prompt:
        return "我多次尝试列出可访问文件，但当前工具返回的文件视图仍然为空。"
    return (
        "我已经尝试使用可用工具处理这个问题，但执行过程没有在预期步数内收敛。"
        "请换一个更具体的问法，或稍后重试。"
    )
