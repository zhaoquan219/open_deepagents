from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent
    / "packages"
    / "contracts"
    / "deepagents-sse-event.json"
)

EVENT_TYPES = {
    "status",
    "message.delta",
    "message.final",
    "step",
    "tool",
    "skill",
    "subagent",
    "sandbox",
    "error",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ContractValidationError(ValueError):
    """Raised when an SSE envelope does not match the scaffold contract."""


@dataclass(frozen=True)
class ContractDefinition:
    required_fields: tuple[str, ...]
    event_types: tuple[str, ...]


def load_contract_definition() -> ContractDefinition:
    raw_contract = json.loads(CONTRACT_PATH.read_text())
    return ContractDefinition(
        required_fields=tuple(raw_contract["required"]),
        event_types=tuple(raw_contract["properties"]["type"]["enum"]),
    )


def _require_string(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be a non-empty string")


def _require_timestamp(name: str, value: Any) -> None:
    _require_string(name, value)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{name} must be an ISO-8601 datetime") from exc


def validate_sse_event(event: Mapping[str, Any]) -> None:
    definition = load_contract_definition()
    missing = [field for field in definition.required_fields if field not in event]
    if missing:
        raise ContractValidationError(f"missing required field(s): {', '.join(missing)}")

    for field_name in ("event_id", "type", "run_id", "session_id", "timestamp"):
        _require_string(field_name, event[field_name])
    if event.get("id") is not None:
        _require_string("id", event["id"])
    if event["type"] not in EVENT_TYPES:
        raise ContractValidationError(f"unsupported type: {event['type']}")
    _require_timestamp("timestamp", event["timestamp"])
    if not isinstance(event["data"], dict):
        raise ContractValidationError("data must be an object")

    if event["type"] == "status":
        status = event.get("status") or event["data"].get("status")
        _require_string("status", status)
    if event["type"] == "message.delta" and not (
        event.get("delta") or event["data"].get("delta") or event["data"].get("text")
    ):
        raise ContractValidationError("message.delta events must include delta text")
    if event["type"] == "message.final":
        message = event.get("message") or event["data"].get("message")
        if not isinstance(message, dict) or not message.get("role"):
            raise ContractValidationError("message.final events must include a message object")


def _event_sequence(event: Mapping[str, Any]) -> int:
    raw = event.get("id") or event["event_id"]
    try:
        return int(str(raw))
    except ValueError as exc:
        raise ContractValidationError("event id must be numeric for sequence validation") from exc


def validate_event_sequence(events: Iterable[Mapping[str, Any]]) -> None:
    latest_by_session: dict[str, int] = {}
    for index, event in enumerate(events, start=1):
        validate_sse_event(event)
        session_id = event["session_id"]
        sequence = _event_sequence(event)
        previous = latest_by_session.get(session_id)
        if previous is not None and sequence <= previous:
            raise ContractValidationError(
                f"event #{index} for session {session_id!r} is out of order: {sequence} <= {previous}"
            )
        latest_by_session[session_id] = sequence
