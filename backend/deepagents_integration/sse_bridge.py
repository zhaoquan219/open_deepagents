from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Mapping, Protocol


SSE_SCHEMA_VERSION = "2026-04-12"


class SupportsAstreamEvents(Protocol):
    def astream_events(self, agent_input: Any, *, version: str = "v2", config: Any = None) -> AsyncIterator[Mapping[str, Any]]:
        ...


@dataclass(frozen=True)
class SseEventEnvelope:
    schema_version: str
    event_id: str
    event: str
    run_id: str
    sequence: int
    data: dict[str, Any]

    def to_sse(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False)
        return f"id: {self.event_id}\nevent: {self.event}\ndata: {payload}\n\n"


async def stream_sse_envelopes(
    runtime: SupportsAstreamEvents,
    agent_input: Any,
    *,
    bridge_run_id: str,
    config: Any = None,
) -> AsyncIterator[SseEventEnvelope]:
    sequence = 1
    yield _make_envelope(
        bridge_run_id=bridge_run_id,
        sequence=sequence,
        event="bridge.hello",
        data={
            "schema_version": SSE_SCHEMA_VERSION,
            "canonical_transcript": False,
            "transient": True,
        },
    )

    async for raw_event in runtime.astream_events(agent_input, version="v2", config=config):
        sequence += 1
        normalized = normalize_runtime_event(raw_event, bridge_run_id=bridge_run_id, sequence=sequence)
        if normalized is not None:
            yield normalized


def normalize_runtime_event(
    raw_event: Mapping[str, Any],
    *,
    bridge_run_id: str,
    sequence: int,
) -> SseEventEnvelope | None:
    event_name = str(raw_event.get("event", ""))
    data = _mapping(raw_event.get("data"))
    metadata = _mapping(raw_event.get("metadata"))
    runtime_run_id = str(raw_event.get("run_id") or bridge_run_id)
    node_name = metadata.get("langgraph_node") or raw_event.get("name") or "runtime"

    if event_name == "on_chain_start":
        return _make_envelope(
            bridge_run_id=bridge_run_id,
            sequence=sequence,
            event="run.started" if sequence <= 2 else "step.started",
            data={
                "runtime_run_id": runtime_run_id,
                "node": node_name,
                "input": _json_safe(data.get("input")),
                "canonical_transcript": False,
                "transient": False,
            },
        )

    if event_name == "on_chain_end":
        return _make_envelope(
            bridge_run_id=bridge_run_id,
            sequence=sequence,
            event="run.completed",
            data={
                "runtime_run_id": runtime_run_id,
                "node": node_name,
                "output": _json_safe(data.get("output")),
                "canonical_transcript": False,
                "transient": False,
            },
        )

    if event_name == "on_chain_error":
        return _make_envelope(
            bridge_run_id=bridge_run_id,
            sequence=sequence,
            event="run.failed",
            data={
                "runtime_run_id": runtime_run_id,
                "node": node_name,
                "error": _json_safe(data.get("error") or raw_event.get("error")),
                "canonical_transcript": False,
                "transient": False,
            },
        )

    if event_name == "on_chat_model_stream":
        delta = _extract_text(data.get("chunk")) or _extract_text(data.get("output"))
        if delta:
            return _make_envelope(
                bridge_run_id=bridge_run_id,
                sequence=sequence,
                event="message.delta",
                data={
                    "runtime_run_id": runtime_run_id,
                    "node": node_name,
                    "text": delta,
                    "canonical_transcript": False,
                    "transient": True,
                },
            )
        return None

    if event_name == "on_chat_model_end":
        final_text = _extract_text(data.get("output"))
        return _make_envelope(
            bridge_run_id=bridge_run_id,
            sequence=sequence,
            event="message.completed",
            data={
                "runtime_run_id": runtime_run_id,
                "node": node_name,
                "text": final_text,
                "canonical_transcript": True,
                "transient": False,
            },
        )

    if event_name in {"on_tool_start", "on_tool_end"}:
        category = _tool_category(str(raw_event.get("name") or "tool"))
        phase = "started" if event_name.endswith("start") else "completed"
        payload_key = "input" if phase == "started" else "output"
        return _make_envelope(
            bridge_run_id=bridge_run_id,
            sequence=sequence,
            event=f"{category}.{phase}",
            data={
                "runtime_run_id": runtime_run_id,
                "name": raw_event.get("name") or category,
                payload_key: _json_safe(data.get(payload_key)),
                "canonical_transcript": False,
                "transient": False,
            },
        )

    return _make_envelope(
        bridge_run_id=bridge_run_id,
        sequence=sequence,
        event="run.progress",
        data={
            "runtime_run_id": runtime_run_id,
            "event": event_name,
            "node": node_name,
            "payload": _json_safe(data),
            "canonical_transcript": False,
            "transient": False,
        },
    )


def validate_sse_event(payload: Mapping[str, Any]) -> None:
    required_top_level = {
        "schema_version": str,
        "event_id": str,
        "event": str,
        "run_id": str,
        "sequence": int,
        "data": dict,
    }
    for key, expected_type in required_top_level.items():
        if key not in payload:
            raise ValueError(f"Missing required SSE field: {key}")
        if not isinstance(payload[key], expected_type):
            raise ValueError(f"SSE field {key!r} must be of type {expected_type.__name__}")
    if payload["schema_version"] != SSE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported SSE schema version: {payload['schema_version']!r}")
    if payload["sequence"] < 1:
        raise ValueError("SSE event sequence must be >= 1")
    data = payload["data"]
    for required_data_key in ("canonical_transcript", "transient"):
        if required_data_key not in data or not isinstance(data[required_data_key], bool):
            raise ValueError(f"SSE event data must include boolean {required_data_key!r}")


def _make_envelope(*, bridge_run_id: str, sequence: int, event: str, data: dict[str, Any]) -> SseEventEnvelope:
    envelope = SseEventEnvelope(
        schema_version=SSE_SCHEMA_VERSION,
        event_id=f"{bridge_run_id}:{sequence:06d}",
        event=event,
        run_id=bridge_run_id,
        sequence=sequence,
        data=data,
    )
    validate_sse_event(asdict(envelope))
    return envelope


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _tool_category(name: str) -> str:
    if name == "execute":
        return "sandbox"
    if name == "task":
        return "skill"
    return "tool"


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return _extract_text(value) or repr(value)


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "messages" in value and isinstance(value["messages"], list) and value["messages"]:
            return _extract_text(value["messages"][-1])
        if "content" in value:
            return _extract_text(value["content"])
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif hasattr(item, "get") and isinstance(item.get("text"), str):
                parts.append(item.get("text"))
        return "".join(parts)
    if isinstance(value, list):
        return "".join(_extract_text(item) for item in value)
    return str(value) if value is not None and not isinstance(value, (bytes, bytearray)) else ""
