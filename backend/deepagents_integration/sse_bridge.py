from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

SSE_SCHEMA_VERSION = "2026-04-12"
MAX_RUNTIME_EVENT_STRING_CHARS = 2048
MAX_RUNTIME_EVENT_COLLECTION_ITEMS = 25
MAX_RUNTIME_EVENT_DEPTH = 6
BASE64_LIKE_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


class SupportsAstreamEvents(Protocol):
    def astream_events(
        self,
        agent_input: Any,
        *,
        version: str = "v2",
        config: Any = None,
        context: Any = None,
    ) -> AsyncIterator[Mapping[str, Any]]:
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
    context: Any = None,
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

    if context is None:
        event_stream = runtime.astream_events(agent_input, version="v2", config=config)
    else:
        event_stream = runtime.astream_events(
            agent_input,
            version="v2",
            config=config,
            context=context,
        )
    async for raw_event in event_stream:
        sequence += 1
        normalized = normalize_runtime_event(
            raw_event,
            bridge_run_id=bridge_run_id,
            sequence=sequence,
        )
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


def _make_envelope(
    *,
    bridge_run_id: str,
    sequence: int,
    event: str,
    data: dict[str, Any],
) -> SseEventEnvelope:
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
    return _bounded_runtime_payload(value)


def _bounded_runtime_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_RUNTIME_EVENT_DEPTH:
        return "[omitted nested runtime payload]"
    if isinstance(value, bytes | bytearray | memoryview):
        payload = bytes(value)
        return {
            "omitted": "binary",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    if isinstance(value, str):
        return _bounded_runtime_string(value)
    if isinstance(value, Mapping):
        items = list(value.items())
        result: dict[str, Any] = {}
        for key, child in items[:MAX_RUNTIME_EVENT_COLLECTION_ITEMS]:
            result[str(key)] = _bounded_runtime_payload(child, depth=depth + 1)
        omitted = len(items) - MAX_RUNTIME_EVENT_COLLECTION_ITEMS
        if omitted > 0:
            result["__omitted_keys__"] = omitted
        return result
    if isinstance(value, list | tuple | set):
        items = list(value)
        list_result = [
            _bounded_runtime_payload(item, depth=depth + 1)
            for item in items[:MAX_RUNTIME_EVENT_COLLECTION_ITEMS]
        ]
        omitted = len(items) - MAX_RUNTIME_EVENT_COLLECTION_ITEMS
        if omitted > 0:
            list_result.append({"__omitted_items__": omitted})
        return list_result
    try:
        json.dumps(value)
        return value
    except TypeError:
        text = _extract_text(value)
        if text:
            return _bounded_runtime_string(text)
        return _bounded_runtime_string(repr(value))


def _bounded_runtime_string(value: str) -> str:
    if _looks_like_base64(value):
        return f"[redacted base64-like runtime string: {len(value)} chars]"
    if len(value) > MAX_RUNTIME_EVENT_STRING_CHARS:
        return f"[omitted long runtime string: {len(value)} chars]"
    return value


def _looks_like_base64(value: str) -> bool:
    compact = "".join(value.split())
    if len(compact) < 256 or len(compact) % 4 != 0:
        return False
    if not BASE64_LIKE_RE.match(value):
        return False
    return any(char in compact for char in "+/=")


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
    if value is None or isinstance(value, bytes | bytearray):
        return ""
    return str(value)
