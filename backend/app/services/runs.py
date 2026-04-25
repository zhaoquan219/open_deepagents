from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.core.logging import format_log_message
from app.core.runtime_catalog import RuntimeSelection
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
    RunEventViewRecord,
    SessionRecord,
    SessionRuntimeLinkRecord,
)
from app.services.run_attachments import (
    InvalidRunAttachmentError,
    persist_generated_state_outputs,
)
from app.services.run_attachments import (
    append_message_attachments as _append_message_attachments,
)
from app.services.run_attachments import (
    generated_state_output_files as _generated_state_output_files,
)
from app.services.run_attachments import (
    link_uploads_to_message as _link_uploads_to_message,
)
from app.services.run_attachments import (
    message_attachments as _message_attachments,
)
from app.services.run_attachments import (
    pending_upload_records_for_attachments as _pending_upload_records_for_attachments,
)
from app.services.run_attachments import (
    resolve_run_attachments as _resolve_run_attachments,
)
from app.services.run_attachments import (
    state_backend_files as _state_backend_files,
)
from app.services.run_events import (
    RunEventViewBuffer,
    utc_now,
)
from app.services.run_events import (
    backlog_after as _backlog_after,
)
from app.services.run_events import (
    bridge_to_ui as _bridge_to_ui,
)
from app.services.run_events import (
    count_runtime_events as _count_runtime_events,
)
from app.services.run_events import (
    extract_message_text as _extract_message_text,
)
from app.services.run_events import (
    is_runtime_placeholder_text as _is_runtime_placeholder_text,
)
from app.services.run_events import (
    persist_event_views as _persist_event_views,
)
from app.services.run_events import (
    restore_subagent_detail as _restore_subagent_detail,
)
from app.services.run_events import (
    to_sse as _to_sse,
)
from app.services.run_events import (
    ui_envelope as _ui_envelope,
)
from app.services.run_history import (
    PersistedRunState,
    load_persisted_run_state,
    stream_persisted_run,
)
from app.services.runtime_overrides import (
    RuntimeOverrides,
    apply_system_prompt_overrides,
    apply_user_prompt_overrides,
    normalize_persisted_extra,
    resolve_runtime_overrides,
)
from app.services.session_titles import sync_session_title_from_source
from deepagents_integration import DeepAgentsRuntimeConfig, stream_sse_envelopes
from deepagents_integration.run_hooks import RunInputHookContext, apply_run_input_hooks

RunBuilder = Callable[[DeepAgentsRuntimeConfig], Any]
logger = logging.getLogger(__name__)
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
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
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
            return _backlog_after(list(self.envelopes), last_event_id)

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
        extra: dict[str, Any] | None = None,
        runtime_selection: RuntimeSelection | None = None,
    ) -> RunState:
        try:
            safe_runtime_selection = dict(
                settings.to_runtime_config(selection=runtime_selection).runtime_selection or {}
            )
        except ValueError as exc:
            raise InvalidRunAttachmentError(str(exc), status_code=400) from exc
        normalized_extra = normalize_persisted_extra(extra)
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
            state.extra = normalized_extra
            session.last_run_id = state.run_id
            sync_session_title_from_source(session, prompt)
            user_message = MessageRecord(
                session_id=session_id,
                role="user",
                content=prompt,
                run_id=state.run_id,
                extra={**normalized_extra, "attachments": resolved_attachments},
            )
            db.add(
                AgentRunRecord(
                    id=state.run_id,
                    session_id=session_id,
                    status="queued",
                    prompt=prompt,
                    extra={
                        **normalized_extra,
                        "attachments": resolved_attachments,
                        "runtime_selection": safe_runtime_selection,
                    },
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
                data={
                    "attachments": resolved_attachments,
                    "prompt": prompt,
                    "extra": normalized_extra,
                },
            )
        )
        self._launch_run(
            settings=settings,
            run_id=state.run_id,
            session_id=session_id,
            prompt=prompt,
            attachments=resolved_attachments,
            extra=normalized_extra,
            runtime_selection=runtime_selection,
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
        extra: dict[str, Any],
        runtime_selection: RuntimeSelection | None,
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
                    extra=extra,
                    runtime_selection=runtime_selection,
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
        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

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

    def get_run(self, *, run_id: str) -> RunState | PersistedRunState:
        state = self.manager.get(run_id)
        if state is not None:
            return state
        persisted = load_persisted_run_state(self.database, run_id=run_id)
        if persisted is None:
            raise KeyError(run_id)
        return persisted

    async def stream(self, *, run_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        state = self.manager.get(run_id)
        if state is not None:
            async for event in self.manager.stream(run_id, last_event_id=last_event_id):
                yield event
            return
        async for event in stream_persisted_run(
            self.database,
            run_id=run_id,
            last_event_id=last_event_id,
        ):
            yield event

    async def _execute_run(
        self,
        *,
        settings: Settings,
        run_id: str,
        session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
        extra: dict[str, Any],
        runtime_selection: RuntimeSelection | None,
    ) -> None:
        state = self.manager.get(run_id)
        if state is None:
            return
        if state.status == "cancelled" or state.completed:
            return

        started_at = time.perf_counter()
        runtime_event_counts: Counter[str] = Counter()
        agent_input: dict[str, Any] = {"messages": []}
        phase = "starting execution"
        event_view_buffer = RunEventViewBuffer(
            self.database,
            run_id=run_id,
            session_id=session_id,
        )
        last_synced_runtime_run_id = ""

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
            runtime_config = settings.to_runtime_config(selection=runtime_selection)
            runtime_overrides = self._runtime_overrides(
                settings=settings,
                session_id=session_id,
                run_id=run_id,
                run_extra=extra,
            )
            runtime_config = replace(
                runtime_config,
                system_prompt=apply_system_prompt_overrides(
                    runtime_config.system_prompt,
                    runtime_overrides,
                ),
            )
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
                raise RuntimeError(
                    "DEEPAGENTS_DEFAULT_MODEL is not configured or cannot be resolved"
                )

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
                settings=settings,
                session_id=session_id,
                run_id=run_id,
                prompt=prompt,
                attachments=attachments,
                runtime_overrides=runtime_overrides,
                hooks=runtime_config.run_input_hooks,
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
                state_file_count=len(agent_input.get("files", {})),
            )

            final_message = ""
            streamed_message_parts: list[str] = []
            saw_message_delta = False
            assistant_message_count = 0
            last_assistant_message = ""
            last_assistant_record_id: str | None = None
            pending_completion_event: dict[str, Any] | None = None
            raw_completion_output: Any = None
            subagent_names_by_runtime_run: dict[str, str] = {}
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
                    **runtime_overrides.context_fields(),
                },
            ):
                if state.status == "cancelled" or state.completed:
                    return
                runtime_event_counts[envelope.event] += 1
                if envelope.event == "run.completed":
                    raw_completion_output = getattr(envelope, "internal_data", {}).get("raw_output")
                sequence += 1
                ui_event = _bridge_to_ui(
                    envelope=envelope,
                    session_id=session_id,
                    sequence=sequence,
                )
                if ui_event["type"] == "subagent":
                    _restore_subagent_detail(
                        ui_event,
                        subagent_names_by_runtime_run=subagent_names_by_runtime_run,
                    )
                if state.status == "cancelled" or state.completed:
                    return
                if ui_event["type"] == "status" and ui_event["status"] == "completed":
                    pending_completion_event = ui_event
                else:
                    event_view_buffer.add(ui_event)
                    state.publish(ui_event)
                if ui_event["type"] == "message.delta" and ui_event.get("delta"):
                    saw_message_delta = True
                    streamed_message_parts.append(str(ui_event.get("delta") or ""))
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
                elif (
                    not final_message
                    and ui_event.get("label") in {"message.completed", "run.completed"}
                    and (candidate := _extract_message_text(ui_event.get("data")))
                    and not _is_runtime_placeholder_text(candidate)
                ):
                    final_message = candidate
                runtime_run_id = str(ui_event.get("data", {}).get("runtime_run_id") or "")
                if runtime_run_id and runtime_run_id != last_synced_runtime_run_id:
                    self._sync_runtime_link(
                        run_id=run_id,
                        session_id=session_id,
                        runtime_run_id=runtime_run_id,
                    )
                    last_synced_runtime_run_id = runtime_run_id

            if state.status == "cancelled" or state.completed:
                return
            if not final_message and streamed_message_parts:
                final_message = "".join(streamed_message_parts)
            event_view_buffer.flush()
            completion_data: dict[str, Any] = (
                dict(pending_completion_event.get("data") or {})
                if pending_completion_event is not None
                else {}
            )
            generated_attachments: list[dict[str, Any]] = []
            if settings.deepagents_sandbox_kind == "state":
                generated_state_files = _generated_state_output_files(
                    raw_completion_output if raw_completion_output is not None else completion_data,
                    initial_files=agent_input.get("files", {}),
                )
                if generated_state_files:
                    generated_attachments = persist_generated_state_outputs(
                        database=self.database,
                        settings=settings,
                        session_id=session_id,
                        run_id=run_id,
                        files=generated_state_files,
                    )
                    completion_data = {
                        **completion_data,
                        "generated_attachments": generated_attachments,
                    }

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
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                skipped_event_view_count=event_view_buffer.skipped_count,
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                persisted_event_view_count=event_view_buffer.persisted_count,
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
                event_view_buffer.add(delta_event)
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
                        "attachments": generated_attachments,
                    },
                )
                event_view_buffer.add(final_event)
                state.publish(final_event)

            event_view_buffer.flush()
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
                            _append_message_attachments(existing_record, generated_attachments)
                            _link_uploads_to_message(
                                db,
                                attachments=generated_attachments,
                                message_id=existing_record.id,
                            )
                            db.add(existing_record)
                    else:
                        assistant_record = MessageRecord(
                            session_id=session_id,
                            role="assistant",
                            content=final_message,
                            run_id=run_id,
                            is_final=True,
                        )
                        _append_message_attachments(assistant_record, generated_attachments)
                        db.add(assistant_record)
                        db.flush()
                        _link_uploads_to_message(
                            db,
                            attachments=generated_attachments,
                            message_id=assistant_record.id,
                        )
                session.last_run_id = run_id
                run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
                if run_record is not None:
                    run_record.status = "completed"
                    run_record.final_output_text = final_message or None
                    run_record.event_count = len(state.envelopes)
                    run_record.completed_at = utc_now()
                    if generated_attachments:
                        run_record.extra = {
                            **(run_record.extra or {}),
                            "generated_attachments": generated_attachments,
                        }
                    db.add(run_record)
                db.add(session)
                db.commit()

            phase = "finalizing completion"
            completion_label = "Run completed"
            completion_detail = "DeepAgents run finished successfully."
            if pending_completion_event is not None:
                completion_label = str(pending_completion_event.get("label") or completion_label)
                completion_detail = str(
                    pending_completion_event.get("detail") or completion_detail
                )
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
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="completed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
                skipped_event_view_count=event_view_buffer.skipped_count,
                persisted_event_view_count=event_view_buffer.persisted_count + 1,
            )
        except asyncio.CancelledError:
            event_view_buffer.flush()
            if not state.cancel_requested and state.status != "cancelled":
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
            )
            return
        except GraphRecursionError as exc:
            event_view_buffer.flush()
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
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="completed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
            )
        except Exception as exc:
            event_view_buffer.flush()
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
                sandbox_event_count=_count_runtime_events(runtime_event_counts, "sandbox."),
                skill_event_count=_count_runtime_events(runtime_event_counts, "skill."),
                status="failed",
                tool_event_count=_count_runtime_events(runtime_event_counts, "tool."),
                ui_event_count=len(state.envelopes),
            )
        finally:
            event_view_buffer.flush()

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

    def _runtime_overrides(
        self,
        *,
        settings: Settings,
        session_id: str,
        run_id: str,
        run_extra: dict[str, Any],
    ) -> RuntimeOverrides:
        with self.database.session_factory() as db:
            session = _require_session(db, session_id)
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            session_extra = dict(session.extra or {})
            persisted_run_extra = dict(run_record.extra or {}) if run_record is not None else {}
        return resolve_runtime_overrides(
            default_timezone=settings.app_timezone,
            session_extra=session_extra,
            run_extra={**persisted_run_extra, **run_extra},
        )

    def _build_agent_input(
        self,
        *,
        settings: Settings,
        session_id: str,
        run_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
        runtime_overrides: RuntimeOverrides,
        hooks: tuple[Any, ...] = (),
    ) -> dict[str, Any]:
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
                    hooks=hooks,
                )
                content = apply_user_prompt_overrides(
                    content,
                    overrides=runtime_overrides,
                    is_current=record.run_id == run_id,
                )
            if not content.strip():
                continue
            messages.append({"role": record.role, "content": content})

        if not messages:
            messages = [
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
                        hooks=hooks,
                    ),
                }
            ]
            messages[0]["content"] = apply_user_prompt_overrides(
                messages[0]["content"],
                overrides=runtime_overrides,
                is_current=True,
            )

        agent_input: dict[str, Any] = {"messages": messages}
        state_files = (
            _state_backend_files(attachments=attachments)
            if settings.deepagents_sandbox_kind == "state"
            else {}
        )
        if state_files:
            agent_input["files"] = state_files
        return agent_input

    def _persist_event(self, *, run_id: str, session_id: str, envelope: dict[str, Any]) -> None:
        _persist_event_views(
            self.database,
            run_id=run_id,
            session_id=session_id,
            envelopes=[envelope],
        )

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
                    next_step="inspect run persistence ordering if runtime links stop attaching",
                )
            else:
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


def _build_recursion_fallback(_prompt: str, _messages: list[dict[str, str]]) -> str:
    return (
        "我已经尝试使用可用工具处理这个问题，但执行过程没有在预期步数内收敛。"
        "请换一个更具体的问法，或稍后重试。"
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
