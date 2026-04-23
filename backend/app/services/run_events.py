from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.core.database import DatabaseState
from app.db.models import AgentRunRecord, RunEventViewRecord
from deepagents_integration import SseEventEnvelope


def utc_now() -> datetime:
    return datetime.now(UTC)


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
        "timestamp": utc_now().isoformat(),
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
