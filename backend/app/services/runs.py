from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from typing import Any

from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import DatabaseState
from app.core.logging import format_log_message
from app.core.runtime_catalog import RuntimeSelection
from app.core.session_scope import (
    PromptInjectionService,
    ensure_runtime_thread_id,
    require_session_record,
    sync_session_title_from_source,
)
from app.db.models import (
    AgentRunRecord,
    MessageRecord,
)
from app.services import run_attachments
from app.services import run_events as events
from app.services.run_events import (
    MAX_REPLAY_BACKLOG_EVENTS,
    PersistedRunState,
    RunManager,
    RunState,
    load_persisted_run_state,
    stream_persisted_run,
)
from deepagents_integration import DeepAgentsRuntimeConfig, stream_sse_envelopes

RunBuilder = Callable[[DeepAgentsRuntimeConfig], Any]
__all__ = ["MAX_REPLAY_BACKLOG_EVENTS", "RunManager", "RunService", "RunState"]
logger = logging.getLogger(__name__)


class RunService:
    def __init__(self, database: DatabaseState, manager: RunManager, builder: RunBuilder) -> None:
        self.database = database
        self.manager = manager
        self.builder = builder
        self.prompt_injections = PromptInjectionService(database)

    def start_run(
        self,
        *,
        settings: Settings,
        session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
        runtime_selection: RuntimeSelection | None = None,
    ) -> RunState:
        try:
            safe_runtime_selection = dict(
                settings.to_runtime_config(selection=runtime_selection).runtime_selection or {}
            )
        except ValueError as exc:
            raise run_attachments.InvalidRunAttachmentError(str(exc), status_code=400) from exc
        with self.database.session_factory() as db:
            session = require_session_record(db, session_id=session_id)
            resolved_attachments = run_attachments.resolve_run_attachments(
                db=db,
                session_id=session_id,
                attachments=attachments,
                settings=settings,
            )
            attachment_records = run_attachments.pending_upload_records_for_attachments(
                db=db,
                session_id=session_id,
                attachments=resolved_attachments,
            )
            state = self.manager.create(session_id)
            runtime_thread_id = ensure_runtime_thread_id(db, session)
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
                    extra={
                        "attachments": resolved_attachments,
                        "runtime_selection": safe_runtime_selection,
                        "runtime_thread_id": runtime_thread_id,
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
            f"run started with {events.count_phrase(len(attachments), 'attachment')}",
            event="run.created",
            phase="queued",
            run_id=state.run_id,
            session_id=session_id,
            attachment_count=len(attachments),
            prompt_chars=len(prompt),
        )
        state.publish(
            events.ui_envelope(
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
            runtime_selection=runtime_selection,
            runtime_thread_id=runtime_thread_id,
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
        runtime_selection: RuntimeSelection | None,
        runtime_thread_id: str,
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
                    runtime_selection=runtime_selection,
                    runtime_thread_id=runtime_thread_id,
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
        runtime_selection: RuntimeSelection | None,
        runtime_thread_id: str,
    ) -> None:
        state = self.manager.get(run_id)
        if state is None:
            return
        if _run_inactive(state):
            return

        started_at = time.perf_counter()
        runtime_event_counts: Counter[str] = Counter()
        agent_input: dict[str, Any] = {"messages": []}
        phase = "starting execution"
        event_view_buffer = events.RunEventViewBuffer(
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
            runtime_summary_message, runtime_summary_fields = events.runtime_config_log_summary(
                settings
            )
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
                raise RuntimeError("No default model could be resolved from models.json")

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
            agent_input = run_attachments.build_agent_input(
                settings=settings,
                records=self.prompt_injections.assemble_prompt_messages(
                    session_id=session_id,
                    run_id=run_id,
                ),
                session_id=session_id,
                run_id=run_id,
                prompt=prompt,
                attachments=attachments,
                hooks=runtime_config.run_input_hooks,
            )
            phase = "streaming"
            run_context = run_attachments.runtime_context(
                session_id=session_id,
                run_id=run_id,
                attachments=attachments,
            )
            _log_run(
                logging.INFO,
                "stream started with "
                f"{events.count_phrase(len(agent_input['messages']), 'input message')} and "
                f"{events.count_phrase(len(attachments), 'attachment')}",
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
                config={
                    "recursion_limit": settings.deepagents_recursion_limit,
                    "configurable": {"thread_id": runtime_thread_id},
                },
                context=run_context,
                event_idle_timeout=settings.deepagents_stream_idle_timeout,
            ):
                if _run_inactive(state):
                    return
                runtime_event_counts[envelope.event] += 1
                if envelope.event == "run.completed":
                    raw_completion_output = getattr(envelope, "internal_data", {}).get("raw_output")
                sequence += 1
                ui_event = events.bridge_to_ui(
                    envelope=envelope,
                    session_id=session_id,
                    sequence=sequence,
                )
                if ui_event["type"] == "subagent":
                    events.restore_subagent_detail(
                        ui_event,
                        subagent_names_by_runtime_run=subagent_names_by_runtime_run,
                    )
                if _run_inactive(state):
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
                        last_assistant_record_id = events.create_message_record(
                            self.database,
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
                    and (candidate := events.extract_message_text(ui_event.get("data")))
                    and not events.is_runtime_placeholder_text(candidate)
                ):
                    final_message = candidate
                runtime_run_id = str(ui_event.get("data", {}).get("runtime_run_id") or "")
                if runtime_run_id and runtime_run_id != last_synced_runtime_run_id:
                    runtime_linked = events.persist_runtime_link(
                        self.database,
                        run_id=run_id,
                        session_id=session_id,
                        runtime_run_id=runtime_run_id,
                        runtime_thread_id=runtime_thread_id,
                    )
                    if not runtime_linked:
                        _log_run(
                            logging.WARNING,
                            "runtime link resolved before the run record was available",
                            event="run.runtime_link_run_missing",
                            phase="syncing runtime link",
                            run_id=run_id,
                            session_id=session_id,
                            next_step=(
                                "inspect run persistence ordering "
                                "if runtime links stop attaching"
                            ),
                        )
                    last_synced_runtime_run_id = runtime_run_id

            if _run_inactive(state):
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
                generated_state_files = run_attachments.generated_state_output_files(
                    raw_completion_output if raw_completion_output is not None else completion_data,
                    initial_files=agent_input.get("files", {}),
                )
                if generated_state_files:
                    generated_attachments = run_attachments.persist_generated_state_outputs(
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
                f"{events.count_phrase(sum(runtime_event_counts.values()), 'runtime event')} in "
                f"{events.elapsed_ms(started_at)} ms",
                event="run.stream_finished",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                duration_ms=events.elapsed_ms(started_at),
                **_runtime_event_log_fields(
                    runtime_event_counts,
                    event_view_buffer=event_view_buffer,
                ),
            )

            if final_message and not saw_message_delta and assistant_message_count == 0:
                if _run_inactive(state):
                    return
                sequence += 1
                delta_event = events.ui_envelope(
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
                if _run_inactive(state):
                    return
                sequence += 1
                final_event = events.ui_envelope(
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
                        "createdAt": events.event_now().isoformat(),
                        "attachments": generated_attachments,
                    },
                )
                event_view_buffer.add(final_event)
                state.publish(final_event)

            event_view_buffer.flush()
            phase = "persisting final message"
            if _run_inactive(state):
                return
            self._persist_completed_run(
                run_id=run_id,
                session_id=session_id,
                state=state,
                final_message=final_message,
                generated_attachments=generated_attachments,
                last_assistant_message=last_assistant_message,
                last_assistant_record_id=last_assistant_record_id,
            )

            phase = "finalizing completion"
            completion_label = "Run completed"
            completion_detail = "DeepAgents run finished successfully."
            if pending_completion_event is not None:
                completion_label = str(pending_completion_event.get("label") or completion_label)
                completion_detail = str(
                    pending_completion_event.get("detail") or completion_detail
                )
            completion_envelope = events.ui_envelope(
                run_id=run_id,
                session_id=session_id,
                sequence=sequence + 1,
                event_type="status",
                status="completed",
                label=completion_label,
                detail=completion_detail,
                data=completion_data,
            )
            events.publish_persisted_event(
                self.database,
                run_id=run_id,
                session_id=session_id,
                state=state,
                envelope=completion_envelope,
            )
            state.terminalize("completed")
            _log_run(
                logging.INFO,
                "run completed with "
                f"{events.count_phrase(sum(runtime_event_counts.values()), 'runtime event')} in "
                f"{events.elapsed_ms(started_at)} ms",
                event="run.completed",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                assistant_chars=len(final_message),
                duration_ms=events.elapsed_ms(started_at),
                status="completed",
                **_runtime_event_log_fields(
                    runtime_event_counts,
                    state=state,
                    event_view_buffer=event_view_buffer,
                    persisted_event_count_offset=1,
                ),
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
            events.persist_recursion_fallback(
                self.database,
                run_id=run_id,
                session_id=session_id,
                state=state,
                fallback_message=fallback_message,
                warning=str(exc),
            )
            state.terminalize("completed")
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
                duration_ms=events.elapsed_ms(started_at),
                error_type=type(exc).__name__,
                status="completed",
                **_runtime_event_log_fields(runtime_event_counts, state=state),
            )
        except Exception as exc:
            event_view_buffer.flush()
            events.persist_failed_run(
                self.database,
                run_id=run_id,
                session_id=session_id,
                state=state,
                error_text=str(exc),
            )
            state.terminalize("failed")
            _log_run(
                logging.ERROR,
                f"run failed while {phase}",
                event="run.failed",
                phase=phase,
                run_id=run_id,
                session_id=session_id,
                reason=type(exc).__name__,
                next_step=events.phase_failure_hint(phase),
                exc_info=True,
                duration_ms=events.elapsed_ms(started_at),
                error_type=type(exc).__name__,
                status="failed",
                **_runtime_event_log_fields(runtime_event_counts, state=state),
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
        terminalized, _ = events.finalize_cancelled_run(
            self.database,
            run_id=run_id,
            session_id=session_id,
            detail=detail,
            state=state or self.manager.get(run_id),
        )
        return terminalized

    def _persist_completed_run(
        self,
        *,
        run_id: str,
        session_id: str,
        state: RunState,
        final_message: str,
        generated_attachments: list[dict[str, Any]],
        last_assistant_message: str,
        last_assistant_record_id: str | None,
    ) -> None:
        with self.database.session_factory() as db:
            session = require_session_record(db, session_id=session_id)
            if final_message:
                self._persist_final_assistant_message(
                    db=db,
                    session_id=session_id,
                    run_id=run_id,
                    final_message=final_message,
                    generated_attachments=generated_attachments,
                    last_assistant_message=last_assistant_message,
                    last_assistant_record_id=last_assistant_record_id,
                )
            session.last_run_id = run_id
            run_record = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
            if run_record is not None:
                run_record.status = "completed"
                run_record.final_output_text = final_message or None
                run_record.event_count = len(state.envelopes)
                run_record.completed_at = events.event_now()
                if generated_attachments:
                    run_record.extra = {
                        **(run_record.extra or {}),
                        "generated_attachments": generated_attachments,
                    }
                db.add(run_record)
            db.add(session)
            db.commit()

    def _persist_final_assistant_message(
        self,
        *,
        db: Session,
        session_id: str,
        run_id: str,
        final_message: str,
        generated_attachments: list[dict[str, Any]],
        last_assistant_message: str,
        last_assistant_record_id: str | None,
    ) -> None:
        if last_assistant_record_id is not None and last_assistant_message == final_message:
            existing_record = (
                db.query(MessageRecord).filter(MessageRecord.id == last_assistant_record_id).first()
            )
            if existing_record is None:
                return
            existing_record.is_final = True
            run_attachments.append_message_attachments(existing_record, generated_attachments)
            run_attachments.link_uploads_to_message(
                db,
                attachments=generated_attachments,
                message_id=existing_record.id,
            )
            db.add(existing_record)
            return
        assistant_record = MessageRecord(
            session_id=session_id,
            role="assistant",
            content=final_message,
            run_id=run_id,
            is_final=True,
        )
        run_attachments.append_message_attachments(assistant_record, generated_attachments)
        db.add(assistant_record)
        db.flush()
        run_attachments.link_uploads_to_message(
            db,
            attachments=generated_attachments,
            message_id=assistant_record.id,
        )


def _runtime_event_log_fields(
    counter: Counter[str],
    *,
    state: RunState | None = None,
    event_view_buffer: events.RunEventViewBuffer | None = None,
    persisted_event_count_offset: int = 0,
) -> dict[str, Any]:
    fields = {
        "message_delta_events": counter["message.delta"],
        "runtime_event_count": sum(counter.values()),
        "sandbox_event_count": events.count_runtime_events(counter, "sandbox."),
        "skill_event_count": events.count_runtime_events(counter, "skill."),
        "tool_event_count": events.count_runtime_events(counter, "tool."),
    }
    if state is not None:
        fields["ui_event_count"] = len(state.envelopes)
    if event_view_buffer is not None:
        fields["skipped_event_view_count"] = event_view_buffer.skipped_count
        fields["persisted_event_view_count"] = (
            event_view_buffer.persisted_count + persisted_event_count_offset
        )
    return fields


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
    payload = dict(event=event, phase=phase, run_id=run_id, session_id=session_id)
    if reason:
        payload["reason"] = reason
    if next_step:
        payload["next_step"] = next_step
    payload.update(
        {key: value for key, value in fields.items() if value is not None and value != ""}
    )
    logger.log(level, "%s", format_log_message(summary, **payload), exc_info=exc_info)


def _build_recursion_fallback(_prompt: str, _messages: list[dict[str, str]]) -> str:
    return "我已尝试处理这个问题，但执行过程没有在预期步数内收敛。请换个更具体的问法，或稍后重试。"


def _run_inactive(state: RunState) -> bool:
    return state.status == "cancelled" or state.completed
