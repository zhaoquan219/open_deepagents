from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MAX_TEXT = 16384
MAX_ITEMS = 25
MAX_DEPTH = 5


@dataclass(frozen=True)
class SseEventEnvelope:
    type: str
    label: str
    detail: str
    data: dict[str, Any]


def normalize_runtime_event(
    raw_event: Mapping[str, Any],
    *,
    bridge_run_id: str,
    session_id: str = "",
    sequence: int,
) -> SseEventEnvelope | None:
    del session_id
    event = str(raw_event.get("event", ""))
    data = _mapping(raw_event.get("data"))
    metadata = _mapping(raw_event.get("metadata"))
    runtime_run_id = str(raw_event.get("run_id") or bridge_run_id)
    node = metadata.get("langgraph_node") or raw_event.get("name") or "runtime"

    if _is_skill_node(node):
        if event == "on_chain_start":
            return _event(
                "skill",
                "skill.started",
                "Skills preparing",
                runtime_run_id=runtime_run_id,
                node=node,
                status="in_progress",
                input=_safe(data.get("input")),
            )
        if event == "on_chain_end":
            output = data.get("output")
            skills = _skills(output)
            return _event(
                "skill",
                "skill.completed",
                f"{len(skills)} skills ready" if skills else "Skills ready",
                runtime_run_id=runtime_run_id,
                node=node,
                status="completed",
                output=_safe(output),
                skills=_safe(skills),
            )

    if event == "on_chain_start":
        first = sequence <= 2
        return _event(
            "status" if first else "step",
            "run.started" if first else "step.started",
            str(node),
            runtime_run_id=runtime_run_id,
            node=node,
            status="running" if first else "in_progress",
            input=_safe(data.get("input")),
        )
    if event == "on_chain_end":
        output = data.get("output")
        text = _text(output)
        return _event(
            "status",
            "run.completed",
            text or str(node),
            runtime_run_id=runtime_run_id,
            node=node,
            status="completed",
            output=_safe(output),
        )
    if event == "on_chain_error":
        error = _safe(data.get("error") or raw_event.get("error"))
        return _event(
            "error",
            "run.failed",
            str(error) if error else "Runtime failed",
            runtime_run_id=runtime_run_id,
            node=node,
            status="failed",
            error=error,
        )
    if event == "on_chat_model_stream":
        delta = _text(data.get("chunk")) or _text(data.get("output"))
        if not delta:
            return None
        return _event(
            "message.delta",
            "assistant.delta",
            delta,
            transient=True,
            runtime_run_id=runtime_run_id,
            node=node,
            delta=delta,
            text=delta,
        )
    if event == "on_chat_model_end":
        text = _text(data.get("output"))
        return _event(
            "message.final",
            "assistant.message",
            text,
            canonical_transcript=True,
            runtime_run_id=runtime_run_id,
            node=node,
            text=text,
            message={"role": "assistant", "content": text},
        )
    if event in {"on_tool_start", "on_tool_end"}:
        name = str(raw_event.get("name") or "tool")
        kind = "sandbox" if name == "execute" else "subagent" if name == "task" else "tool"
        phase = "started" if event.endswith("start") else "completed"
        payload_key = "input" if phase == "started" else "output"
        return _event(
            kind,
            f"{kind}.{phase}",
            name,
            runtime_run_id=runtime_run_id,
            name=name,
            tool_name=name,
            **{payload_key: _safe(data.get(payload_key))},
        )
    return _event(
        "step",
        "runtime.event",
        event,
        runtime_run_id=runtime_run_id,
        event=event,
        node=node,
        payload=_safe(data),
    )


def _event(event_type: str, label: str, detail: str, **data: Any) -> SseEventEnvelope:
    data.setdefault("canonical_transcript", False)
    data.setdefault("transient", False)
    return SseEventEnvelope(type=event_type, label=label, detail=detail, data=data)


def _is_skill_node(node: Any) -> bool:
    return "SkillsMiddleware" in str(node)


def _skills(output: Any) -> list[Any]:
    if isinstance(output, Mapping) and isinstance(output.get("skills_metadata"), list):
        return list(output["skills_metadata"])
    return []


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "[omitted nested runtime payload]"
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        if len(value) <= MAX_TEXT:
            return value
        return f"[omitted long runtime string: {len(value)} chars]"
    if isinstance(value, bytes | bytearray | memoryview):
        return {"omitted": "binary", "size_bytes": len(value)}
    if isinstance(value, Mapping):
        return {str(key): _safe(child, depth + 1) for key, child in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, list | tuple | set):
        return [_safe(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    text = _text(value)
    if text:
        return _safe(text, depth)
    return f"[omitted non-json runtime object: {value.__class__.__name__}]"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if isinstance(value.get("messages"), list) and value["messages"]:
            return _text(value["messages"][-1])
        for key in ("content", "text"):
            if key in value:
                return _text(value[key])
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_text(item) for item in content)
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    return "" if isinstance(value, bytes | bytearray) else str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
