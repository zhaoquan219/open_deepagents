from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Any

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "packages" / "contracts" / "deepagents-sse-event-v1.json"

EVENT_TYPES = {
    "run.started",
    "run.completed",
    "run.failed",
    "message.delta",
    "message.completed",
    "step.started",
    "step.completed",
    "step.failed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "skill.started",
    "skill.completed",
    "skill.failed",
    "subagent.started",
    "subagent.completed",
    "subagent.failed",
    "sandbox.started",
    "sandbox.completed",
    "sandbox.failed",
    "attachment.started",
    "attachment.completed",
    "attachment.failed",
}
STATUSES = {"started", "in_progress", "completed", "failed"}
MESSAGE_EVENT_TYPES = {"message.delta", "message.completed"}
STEP_SCOPED_EVENT_TYPES = {
    "step.started",
    "step.completed",
    "step.failed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "skill.started",
    "skill.completed",
    "skill.failed",
    "subagent.started",
    "subagent.completed",
    "subagent.failed",
    "sandbox.started",
    "sandbox.completed",
    "sandbox.failed",
    "attachment.started",
    "attachment.completed",
    "attachment.failed",
}


class ContractValidationError(ValueError):
    """Raised when an SSE envelope does not match the scaffold contract."""


@dataclass(frozen=True)
class ContractDefinition:
    version: str
    required_fields: tuple[str, ...]
    event_types: tuple[str, ...]
    statuses: tuple[str, ...]


def load_contract_definition() -> ContractDefinition:
    raw_contract = json.loads(CONTRACT_PATH.read_text())
    return ContractDefinition(
        version=raw_contract["properties"]["event_version"]["const"],
        required_fields=tuple(raw_contract["required"]),
        event_types=tuple(raw_contract["properties"]["event_type"]["enum"]),
        statuses=tuple(raw_contract["properties"]["status"]["enum"]),
    )


def _require_string(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be a non-empty string")


def _require_optional_string(name: str, value: Any) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ContractValidationError(f"{name} must be null or a non-empty string")


def _require_timestamp(name: str, value: Any) -> None:
    _require_string(name, value)
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - defensive branch
        raise ContractValidationError(f"{name} must be an ISO-8601 datetime") from exc


def validate_sse_event(event: Mapping[str, Any]) -> None:
    definition = load_contract_definition()

    missing = [field for field in definition.required_fields if field not in event]
    if missing:
        raise ContractValidationError(f"missing required field(s): {', '.join(missing)}")

    _require_string("event_version", event["event_version"])
    if event["event_version"] != definition.version:
        raise ContractValidationError(
            f"event_version must be {definition.version!r}, got {event['event_version']!r}"
        )

    for field_name in ("session_id", "run_id", "event_type", "status"):
        _require_string(field_name, event[field_name])

    _require_optional_string("message_id", event.get("message_id"))
    _require_optional_string("step_id", event.get("step_id"))

    sequence = event["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ContractValidationError("sequence must be a non-negative integer")

    if event["event_type"] not in EVENT_TYPES:
        raise ContractValidationError(f"unsupported event_type: {event['event_type']}")

    if event["status"] not in STATUSES:
        raise ContractValidationError(f"unsupported status: {event['status']}")

    if event["event_type"] in MESSAGE_EVENT_TYPES and not event.get("message_id"):
        raise ContractValidationError("message events must include message_id")

    if event["event_type"] in STEP_SCOPED_EVENT_TYPES and not event.get("step_id"):
        raise ContractValidationError(
            f"{event['event_type']} events must include step_id"
        )

    _require_timestamp("ts", event["ts"])

    if not isinstance(event["payload"], dict):
        raise ContractValidationError("payload must be an object")


def validate_event_sequence(events: Iterable[Mapping[str, Any]]) -> None:
    latest_by_run: dict[str, int] = {}

    for index, event in enumerate(events, start=1):
        validate_sse_event(event)
        run_id = event["run_id"]
        sequence = event["sequence"]
        previous = latest_by_run.get(run_id)
        if previous is not None and sequence <= previous:
            raise ContractValidationError(
                f"event #{index} for run {run_id!r} is out of order: {sequence} <= {previous}"
            )
        latest_by_run[run_id] = sequence
