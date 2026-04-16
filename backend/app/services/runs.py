from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.core.logging import format_log_message
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
    RunEventViewRecord,
    SessionRecord,
    SessionRuntimeLinkRecord,
    UploadRecord,
)
from app.services.session_titles import sync_session_title_from_source
from deepagents_integration import DeepAgentsRuntimeConfig, SseEventEnvelope, stream_sse_envelopes
from deepagents_integration.run_hooks import RunInputHookContext, apply_run_input_hooks

RunBuilder = Callable[[DeepAgentsRuntimeConfig], Any]
logger = logging.getLogger(__name__)


class InvalidRunAttachmentError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


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
    cancel_requested: bool = False
    execution_loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    execution_task: asyncio.Task[None] | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, envelope: dict[str, Any]) -> bool:
        with self.lock:
            if self.completed:
                return False
            self.envelopes.append(envelope)
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
                self.envelopes.append(envelope)
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
            return _backlog_after(list(self.envelopes), last_event_id)

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
            return len(self.envelopes) + 1


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
            resolved_attachments = _resolve_run_attachments(
                db=db,
                session_id=session_id,
                attachments=attachments,
                settings=settings,
            )
            attachment_records = _pending_upload_records_for_attachments(
                db=db,
                session_id=session_id,
                attachments=resolved_attachments,
            )
            state = self.manager.create(session_id)
            session.last_run_id = state.run_id
            sync_session_title_from_source(session, prompt)
            user_message = MessageRecord(
                session_id=session_id,
                role="user",
                content=prompt,
                run_id=state.run_id,
                extra={"attachments": resolved_attachments},
            )
            db.add(
                AgentRunRecord(
                    id=state.run_id,
                    session_id=session_id,
                    status="queued",
                    prompt=prompt,
                    extra={"attachments": resolved_attachments},
                )
            )
            db.add(user_message)
            db.flush()
            for record in attachment_records:
                record.message_id = user_message.id
                db.add(record)
            db.add(session)
            db.commit()

        _log_run(
            logging.INFO,
            f"run started with {_count_phrase(len(attachments), 'attachment')}",
            event="run.created",
            phase="queued",
            run_id=state.run_id,
            session_id=session_id,
            attachment_count=len(attachments),
            prompt_chars=len(prompt),
        )
        state.publish(
            _ui_envelope(
                run_id=state.run_id,
                session_id=session_id,
                sequence=1,
                event_type="status",
                status="running",
                label="Run started",
                detail="Queued DeepAgents run.",
                data={"attachments": resolved_attachments, "prompt": prompt},
            )
        )
        self._launch_run(
            settings=settings,
            run_id=state.run_id,
            session_id=session_id,
            prompt=prompt,
            attachments=resolved_attachments,
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
            state = self.manager.get(run_id)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            task = loop.create_task(
                self._execute_run(
                    settings=settings,
                    run_id=run_id,
                    session_id=session_id,
                    prompt=prompt,
                    attachments=attachments,
                )
            )
            if state is not None:
                state.bind_execution(loop, task)
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            finally:
                if state is not None:
                    state.clear_execution(task)
                loop.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            return
        loop.run_in_executor(None, runner)

    def cancel_run(self, *, run_id: str) -> RunState:
        state = self.manager.get(run_id)
        if state is None:
            raise KeyError(run_id)

        if state.status in {"completed", "failed", "cancelled"}:
            return state

        detail = "DeepAgents run was stopped by the user."
        terminalized = self._finalize_cancelled(
            run_id=run_id,
            session_id=state.session_id,
            detail=detail,
            state=state,
        )
        if terminalized:
            _log_run(
                logging.INFO,
                "run cancelled by user request",
                event="run.cancelled",
                phase="cancelling execution",
                run_id=run_id,
                session_id=state.session_id,
                status="cancelled",
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
        if state.status == "cancelled" or state.completed:
            return

        started_at = time.perf_counter()
        runtime_event_counts: Counter[str] = Counter()
        runtime_run_id = ""
        agent_input: dict[str, list[dict[str, str]]] = {"messages": []}
        phase = "starting execution"

        try:
            _log_run(
                logging.INFO,
                "run execution started",
                event="run.execution_started",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
            )
            phase = "resolving runtime config"
            runtime_config = settings.to_runtime_config()
            runtime_summary_message, runtime_summary_fields = _runtime_config_log_summary(settings)
            runtime_log_fields: dict[str, Any] = {
                "recursion_limit": settings.deepagents_recursion_limit,
                **runtime_summary_fields,
                **runtime_config.logging_summary(),
            }
            _log_run(
                logging.INFO,
                runtime_summary_message,
                event="run.runtime_config",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                **runtime_log_fields,
            )
            if runtime_config.model is None:
                raise RuntimeError("DEEPAGENTS_MODEL is not configured in backend/.env")

            phase = "building agent"
            _log_run(
                logging.INFO,
                "building DeepAgents agent",
                event="run.agent_build_started",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
            )
            agent = self.builder(runtime_config)
            _log_run(
                logging.INFO,
                "DeepAgents agent is ready",
                event="run.agent_build_completed",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
            )
            phase = "building agent input"
            agent_input = self._build_agent_input(
                session_id=session_id,
                run_id=run_id,
                prompt=prompt,
                attachments=attachments,
                hook_specs=runtime_config.run_input_hook_specs,
            )
            phase = "streaming"
            _log_run(
                logging.INFO,
                "stream started with "
                f"{_count_phrase(len(agent_input['messages']), 'input message')} and "
                f"{_count_phrase(len(attachments), 'attachment')}",
                event="run.stream_started",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                attachment_count=len(attachments),
                message_count=len(agent_input["messages"]),
            )

            final_message = ""
            saw_message_delta = False
            assistant_message_count = 0
            last_assistant_message = ""
            last_assistant_record_id: str | None = None
            pending_completion_event: dict[str, Any] | None = None
            sequence = 1
            async for envelope in stream_sse_envelopes(
                agent,
                agent_input,
                bridge_run_id=run_id,
                config={"recursion_limit": settings.deepagents_recursion_limit},
                context={
                    "session_id": session_id,
                    "run_id": run_id,
                    "current_attachments": tuple(attachments),
                    "attachments": tuple(attachments),
                },
            ):
                if state.status == "cancelled" or state.completed:
                    return
                runtime_event_counts[envelope.event] += 1
                runtime_run_id = str(envelope.data.get("runtime_run_id") or runtime_run_id)
                sequence += 1
                ui_event = _bridge_to_ui(
                    envelope=envelope,
                    session_id=session_id,
                    sequence=sequence,
                )
                if state.status == "cancelled" or state.completed:
                    return
                if ui_event["type"] == "status" and ui_event["status"] == "completed":
                    pending_completion_event = ui_event
                else:
                    self._persist_event(run_id=run_id, session_id=session_id, envelope=ui_event)
                    state.publish(ui_event)
                if ui_event["type"] == "message.delta" and ui_event.get("delta"):
                    saw_message_delta = True
                if ui_event["type"] == "message.final":
                    assistant_content = str(ui_event.get("message", {}).get("content") or "")
                    if assistant_content:
                        assistant_message_count += 1
                        final_message = assistant_content
                        last_assistant_message = assistant_content
                        last_assistant_record_id = self._create_message_record(
                            session_id=session_id,
                            role="assistant",
                            content=assistant_content,
                            run_id=run_id,
                            is_final=False,
                            step_id=str(ui_event.get("step_id") or "") or None,
                            extra={"event_id": str(ui_event["event_id"])},
                        )
                elif candidate := _extract_message_text(ui_event.get("data")):
                    final_message = candidate
                self._sync_runtime_link(
                    run_id=run_id,
                    session_id=session_id,
                    runtime_run_id=str(ui_event.get("data", {}).get("runtime_run_id") or ""),
                )

            if state.status == "cancelled" or state.completed:
                return

            _log_run(
                logging.INFO,
                "stream finished with "
                f"{_count_phrase(sum(runtime_event_counts.values()), 'runtime event')} in "
                f"{_elapsed_ms(started_at)} ms",
                event="run.stream_finished",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                duration_ms=_elapsed_ms(started_at),
                message_delta_events=runtime_event_counts["message.delta"],
                runtime_event_count=sum(runtime_event_counts.values()),
                runtime_run_id=runtime_run_id,
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
            )

            if final_message and not saw_message_delta and assistant_message_count == 0:
                if state.status == "cancelled" or state.completed:
                    return
                sequence += 1
                delta_event = _ui_envelope(
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
                self._persist_event(run_id=run_id, session_id=session_id, envelope=delta_event)
                state.publish(delta_event)

            should_emit_terminal_message = bool(final_message) and (
                assistant_message_count == 0 or final_message != last_assistant_message
            )
            if should_emit_terminal_message:
                if state.status == "cancelled" or state.completed:
                    return
                sequence += 1
                final_event = _ui_envelope(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence,
                        event_type="message.final",
                        status="completed",
                        label="message.final",
                        detail="Assistant response completed.",
                        data={"final": True},
                        message={
                            "id": f"final:{run_id}",
                            "role": "assistant",
                            "content": final_message,
                            "createdAt": utc_now().isoformat(),
                            "attachments": [],
                        },
                    )
                self._persist_event(run_id=run_id, session_id=session_id, envelope=final_event)
                state.publish(final_event)

            phase = "persisting final message"
            if state.status == "cancelled" or state.completed:
                return
            with self.database.session_factory() as db:
                session = _require_session(db, session_id)
                if final_message:
                    if (
                        last_assistant_record_id is not None
                        and last_assistant_message == final_message
                    ):
                        existing_record = (
                            db.query(MessageRecord)
                            .filter(MessageRecord.id == last_assistant_record_id)
                            .first()
                        )
                        if existing_record is not None:
                            existing_record.is_final = True
                            db.add(existing_record)
                    else:
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
                    run_record.final_output_text = final_message or None
                    run_record.event_count = len(state.envelopes)
                    run_record.completed_at = utc_now()
                    db.add(run_record)
                db.add(session)
                db.commit()

            phase = "finalizing completion"
            completion_label = "Run completed"
            completion_detail = "DeepAgents run finished successfully."
            completion_data: dict[str, Any] = {}
            if pending_completion_event is not None:
                completion_label = str(pending_completion_event.get("label") or completion_label)
                completion_detail = str(
                    pending_completion_event.get("detail") or completion_detail
                )
                completion_data = dict(pending_completion_event.get("data") or {})
            completion_envelope = _ui_envelope(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=sequence + 1,
                    event_type="status",
                    status="completed",
                    label=completion_label,
                    detail=completion_detail,
                    data=completion_data,
                )
            self._persist_event(
                run_id=run_id,
                session_id=session_id,
                envelope=completion_envelope,
            )
            state.publish(completion_envelope)
            state.finish("completed")
            if not runtime_run_id:
                _log_run(
                    logging.WARNING,
                    "run completed without receiving an upstream runtime link",
                    event="run.runtime_link_missing",
                    phase=phase,
                    run_id=run_id,
                    session_id=session_id,
                    next_step="inspect upstream runtime envelopes for a missing runtime_run_id",
                    status="completed",
                )
            _log_run(
                logging.INFO,
                "run completed with "
                f"{_count_phrase(sum(runtime_event_counts.values()), 'runtime event')} in "
                f"{_elapsed_ms(started_at)} ms",
                event="run.completed",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                assistant_chars=len(final_message),
                duration_ms=_elapsed_ms(started_at),
                message_delta_events=runtime_event_counts["message.delta"],
                runtime_event_count=sum(runtime_event_counts.values()),
                runtime_run_id=runtime_run_id,
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="completed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
            )
        except asyncio.CancelledError:
            if state.status != "cancelled":
                self._finalize_cancelled(
                    run_id=run_id,
                    session_id=session_id,
                    detail="DeepAgents run was stopped before completion.",
                    state=state,
                )
            _log_run(
                logging.INFO,
                "run execution stopped before completion",
                event="run.execution_cancelled",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                status="cancelled",
                runtime_event_count=sum(runtime_event_counts.values()),
                runtime_run_id=runtime_run_id,
            )
            return
        except GraphRecursionError as exc:
            phase = "persisting fallback response"
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
            if not runtime_run_id:
                _log_run(
                    logging.WARNING,
                    "run completed without receiving an upstream runtime link",
                    event="run.runtime_link_missing",
                    phase=phase,
                    run_id=run_id,
                    session_id=session_id,
                    next_step="inspect upstream runtime envelopes for a missing runtime_run_id",
                    status="completed",
                )
            _log_run(
                logging.WARNING,
                "run completed with a fallback response after the recursion limit was reached",
                event="run.recursion_fallback",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                reason="recursion_limit",
                next_step="inspect recursive tool or agent loops in the upstream runtime trace",
                assistant_chars=len(fallback_message),
                duration_ms=_elapsed_ms(started_at),
                error_type=type(exc).__name__,
                runtime_event_count=sum(runtime_event_counts.values()),
                runtime_run_id=runtime_run_id,
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="completed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
            )
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
            if not runtime_run_id:
                _log_run(
                    logging.WARNING,
                    "run failed before an upstream runtime link was available",
                    event="run.runtime_link_missing",
                    phase=phase,
                    run_id=run_id,
                    session_id=session_id,
                    next_step="inspect upstream runtime envelopes for a missing runtime_run_id",
                    status="failed",
                )
            _log_run(
                logging.ERROR,
                f"run failed while {phase}",
                event="run.failed",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                reason=type(exc).__name__,
                next_step=_phase_failure_hint(phase),
                exc_info=True,
                duration_ms=_elapsed_ms(started_at),
                error_type=type(exc).__name__,
                runtime_event_count=sum(runtime_event_counts.values()),
                runtime_run_id=runtime_run_id,
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="failed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
            )

    def _finalize_cancelled(
        self,
        *,
        run_id: str,
        session_id: str,
        detail: str,
        state: RunState | None = None,
    ) -> bool:
        current_state = state or self.manager.get(run_id)
        if current_state is not None and current_state.status == "cancelled":
            current_state.request_cancel()
            return False

        with self.database.session_factory() as db:
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            if run_record is None:
                raise KeyError(run_id)
            if run_record.status in {"completed", "failed", "cancelled"}:
                if current_state is not None:
                    current_state.request_cancel()
                return False

            sequence = (
                current_state.next_sequence()
                if current_state is not None
                else run_record.event_count + 1
            )
            cancel_envelope = _ui_envelope(
                run_id=run_id,
                session_id=session_id,
                sequence=sequence,
                event_type="status",
                status="cancelled",
                label="Run cancelled",
                detail=detail,
                data={"cancelled_by": "user"},
            )
            record = RunEventViewRecord(
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
            db.merge(record)
            run_record.status = "cancelled"
            run_record.error_text = detail
            run_record.event_count = max(run_record.event_count, sequence)
            run_record.completed_at = utc_now()
            db.add(run_record)
            session = _require_session(db, session_id)
            session.last_run_id = run_id
            db.add(session)
            db.commit()

        if current_state is not None:
            current_state.request_cancel()
            return current_state.terminalize("cancelled", cancel_envelope)
        return True

    def _build_agent_input(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
        hook_specs: tuple[str, ...] = (),
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
            if record.role == "user":
                record_attachments = (
                    attachments
                    if record.run_id == run_id
                    else _message_attachments(record=record)
                )
                content = apply_run_input_hooks(
                    context=RunInputHookContext(
                        session_id=session_id,
                        run_id=run_id,
                        role=record.role,
                        content=content,
                        attachments=tuple(record_attachments),
                        is_current_run=record.run_id == run_id,
                    ),
                    hook_specs=hook_specs,
                )
            if not content.strip():
                continue
            messages.append({"role": record.role, "content": content})

        if messages:
            return {"messages": messages}

        return {
            "messages": [
                {
                    "role": "user",
                    "content": apply_run_input_hooks(
                        context=RunInputHookContext(
                            session_id=session_id,
                            run_id=run_id,
                            role="user",
                            content=prompt,
                            attachments=tuple(attachments),
                            is_current_run=True,
                        ),
                        hook_specs=hook_specs,
                    ),
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

    def _sync_runtime_link(self, *, run_id: str, session_id: str, runtime_run_id: str) -> None:
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
            elif record.runtime_run_id and record.runtime_run_id != runtime_run_id:
                _log_run(
                    logging.WARNING,
                    "session runtime link changed from "
                    f"{record.runtime_run_id} to {runtime_run_id}",
                    event="run.session_runtime_link_changed",
                    phase="syncing runtime link",
                    run_id=run_id,
                    session_id=session_id,
                    previous_runtime_run_id=record.runtime_run_id,
                    runtime_run_id=runtime_run_id,
                )
            record.runtime_run_id = runtime_run_id
            record.last_seen_at = utc_now()
            db.add(record)
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            if run_record is None:
                _log_run(
                    logging.WARNING,
                    "runtime link resolved before the run record was available",
                    event="run.runtime_link_run_missing",
                    phase="syncing runtime link",
                    run_id=run_id,
                    session_id=session_id,
                    runtime_run_id=runtime_run_id,
                    next_step="inspect run persistence ordering if runtime links stop attaching",
                )
            else:
                if run_record.runtime_run_id and run_record.runtime_run_id != runtime_run_id:
                    _log_run(
                        logging.WARNING,
                        "run runtime link changed from "
                        f"{run_record.runtime_run_id} to {runtime_run_id}",
                        event="run.runtime_run_id_changed",
                        phase="syncing runtime link",
                        run_id=run_id,
                        session_id=session_id,
                        previous_runtime_run_id=run_record.runtime_run_id,
                        runtime_run_id=runtime_run_id,
                    )
                run_record.runtime_run_id = runtime_run_id
                db.add(run_record)
            db.commit()

    def _create_message_record(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        is_final: bool = True,
        step_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        with self.database.session_factory() as db:
            session = _require_session(db, session_id)
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


def _log_run(
    level: int,
    summary: str,
    *,
    event: str,
    phase: str,
    run_id: str,
    session_id: str,
    reason: str | None = None,
    next_step: str | None = None,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "phase": phase,
        "run_id": run_id,
        "session_id": session_id,
    }
    if reason:
        payload["reason"] = reason
    if next_step:
        payload["next_step"] = next_step
    payload.update(
        {key: value for key, value in fields.items() if value is not None and value != ""}
    )
    logger.log(level, "%s", format_log_message(summary, **payload), exc_info=exc_info)


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _runtime_config_log_summary(settings: Settings) -> tuple[str, dict[str, object]]:
    fields = settings.runtime_model_logging_summary()
    if fields["selected_model_source"] == "custom_api":
        base_url = fields.get("custom_model_base_url") or "configured base URL"
        return (
            "resolved custom model config using "
            f"{fields.get('selected_model_provider') or 'custom_api'} base URL {base_url} with "
            f"{fields.get('custom_model_headers_count', 0)} default headers",
            fields,
        )
    if fields["selected_model_name"]:
        provider = fields.get("selected_model_provider") or "configured"
        return (
            f"resolved runtime config using {provider} model {fields['selected_model_name']}",
            fields,
        )
    return "resolved runtime config without a configured model", fields


def _phase_failure_hint(phase: str) -> str:
    if phase == "resolving runtime config":
        return "inspect backend model settings and custom API configuration"
    if phase == "building agent":
        return "inspect the DeepAgents builder and runtime dependencies"
    if phase == "streaming":
        return "inspect upstream runtime events, tool calls, and model responses"
    if phase == "persisting final message":
        return "inspect database writes and the final assistant message payload"
    if phase == "persisting fallback response":
        return "inspect fallback message persistence and recursion-limit handling"
    if phase == "finalizing completion":
        return "inspect completion event persistence and run state finalization"
    return "inspect the previous stage log and structured metadata for the failing step"


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
        content = _extract_message_text(data)
        if not content:
            event_type = "step"
            detail = "Model completed without a direct text payload."
        else:
            data = {**data, "final": False}
            message = {
                "id": f"message:{envelope.event_id}",
                "role": "assistant",
                "content": content,
                "createdAt": utc_now().isoformat(),
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


def _resolve_run_attachments(
    *,
    db: Session,
    session_id: str,
    attachments: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    if not attachments:
        return []

    requested_ids = [str(item.get("id") or "").strip() for item in attachments if item.get("id")]
    records_by_id = {}
    if requested_ids:
        records = (
            db.query(UploadRecord)
            .filter(
                UploadRecord.session_id == session_id,
                UploadRecord.id.in_(requested_ids),
            )
            .all()
        )
        records_by_id = {record.id: record for record in records}
    requested_storage_keys = [
        str(item.get("storage_key") or "").strip()
        for item in attachments
        if not item.get("id") and item.get("storage_key")
    ]
    records_by_storage_key = {}
    if requested_storage_keys:
        records = (
            db.query(UploadRecord)
            .filter(
                UploadRecord.session_id == session_id,
                UploadRecord.storage_key.in_(requested_storage_keys),
            )
            .all()
        )
        records_by_storage_key = {record.storage_key: record for record in records}

    resolved: list[dict[str, Any]] = []
    for item in attachments:
        attachment_id = str(item.get("id") or "").strip()
        record = records_by_id.get(attachment_id)
        if attachment_id and record is None:
            raise InvalidRunAttachmentError("Invalid attachment")
        storage_key = str(item.get("storage_key") or "").strip()
        if record is None and storage_key:
            record = records_by_storage_key.get(storage_key)
        if record is not None:
            if record.message_id is not None:
                raise InvalidRunAttachmentError(
                    "Upload is already attached to a message",
                    status_code=409,
                )
            upload_path = _resolve_attachment_disk_path(
                upload_root=settings.upload_storage_dir,
                storage_key=record.storage_key,
            )
            resolved.append(
                {
                    "id": record.id,
                    "name": record.filename,
                    "status": str(item.get("status") or "uploaded"),
                    "size": record.size_bytes,
                    "size_bytes": record.size_bytes,
                    "content_type": record.content_type,
                    "storage_key": record.storage_key,
                    "upload_path": str(upload_path) if upload_path is not None else "",
                    "sandbox_path": _resolve_sandbox_attachment_path(
                        upload_path=upload_path,
                        settings=settings,
                    ),
                }
            )
            continue

        upload_path = _resolve_attachment_disk_path(
            upload_root=settings.upload_storage_dir,
            storage_key=storage_key,
        )
        resolved.append(
            {
                "id": attachment_id or str(item.get("attachment_id") or ""),
                "name": str(item.get("name") or item.get("filename") or "attachment"),
                "status": str(item.get("status") or "uploaded"),
                "size": int(item.get("size") or item.get("size_bytes") or 0),
                "size_bytes": int(item.get("size_bytes") or item.get("size") or 0),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "storage_key": storage_key,
                "upload_path": str(upload_path) if upload_path is not None else "",
                "sandbox_path": _resolve_sandbox_attachment_path(
                    upload_path=upload_path,
                    settings=settings,
                ),
            }
        )

    return resolved


def _pending_upload_records_for_attachments(
    *,
    db: Session,
    session_id: str,
    attachments: list[dict[str, Any]],
) -> list[UploadRecord]:
    upload_ids = [str(item.get("id") or "").strip() for item in attachments if item.get("id")]
    if not upload_ids:
        return []

    records = (
        db.query(UploadRecord)
        .filter(
            UploadRecord.session_id == session_id,
            UploadRecord.id.in_(upload_ids),
        )
        .all()
    )
    records_by_id = {record.id: record for record in records}

    ordered_records: list[UploadRecord] = []
    for upload_id in upload_ids:
        record = records_by_id.get(upload_id)
        if record is None:
            raise InvalidRunAttachmentError("Invalid attachment")
        if record.message_id is not None:
            raise InvalidRunAttachmentError(
                "Upload is already attached to a message",
                status_code=409,
            )
        ordered_records.append(record)
    return ordered_records


def _resolve_attachment_disk_path(*, upload_root: Path, storage_key: str) -> Path | None:
    if not storage_key:
        return None
    return (upload_root / storage_key).resolve()


def _resolve_sandbox_attachment_path(*, upload_path: Path | None, settings: Settings) -> str:
    if upload_path is None:
        return ""

    if settings.deepagents_sandbox_kind == "state":
        return str(upload_path)

    sandbox_root = _resolved_sandbox_root(settings)
    if sandbox_root is None:
        return ""

    try:
        relative_path = upload_path.resolve().relative_to(sandbox_root)
    except ValueError:
        return ""

    normalized = relative_path.as_posix()
    if settings.deepagents_sandbox_virtual_mode:
        return f"/{normalized.lstrip('/')}"
    return normalized


def _resolved_sandbox_root(settings: Settings) -> Path | None:
    if settings.deepagents_sandbox_root_dir is not None:
        return Path(settings.deepagents_sandbox_root_dir).expanduser().resolve()
    if settings.deepagents_sandbox_kind == "filesystem":
        return settings.upload_storage_dir.resolve()
    return None


def _message_attachments(*, record: MessageRecord) -> list[dict[str, Any]]:
    extra = record.extra if isinstance(record.extra, dict) else {}
    attachments = extra.get("attachments")
    if isinstance(attachments, list):
        return [item for item in attachments if isinstance(item, dict)]
    return []


def _build_recursion_fallback(_prompt: str, _messages: list[dict[str, str]]) -> str:
    return (
        "我已经尝试使用可用工具处理这个问题，但执行过程没有在预期步数内收敛。"
        "请换一个更具体的问法，或稍后重试。"
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _count_runtime_events(counter: Counter[str], prefix: str) -> int:
    return sum(count for name, count in counter.items() if name.startswith(prefix))
