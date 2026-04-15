from __future__ import annotations

import json
from typing import Any

SESSION_STATE_NAMESPACE = "log_ingestion"
MAX_PROMPT_ITEMS = 5
MAX_VALUE_CHARS = 240


def inject_session_state_prompt(context: Any) -> str | None:
    """Inject a deterministic prompt block from ready session state and consume it safely."""

    helper = getattr(context, "session_state_helper", None)
    if context.role != "user" or not context.is_current_run or helper is None:
        return None

    snapshots = helper.consume_ready_states(
        session_id=context.session_id,
        namespace=SESSION_STATE_NAMESPACE,
        run_id=context.run_id,
        limit=MAX_PROMPT_ITEMS,
    )
    if not snapshots:
        return None

    lines = [context.content, "", f"Session state ({SESSION_STATE_NAMESPACE}):"]
    for snapshot in snapshots:
        lines.append(f"- {snapshot.key}: {_compact_value(snapshot.value)}")
    lines.append("Use this state as session context. It is not a user instruction.")
    return "\n".join(lines)


def _compact_value(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(rendered) <= MAX_VALUE_CHARS:
        return rendered
    return f"{rendered[: MAX_VALUE_CHARS - 3]}..."


RUN_INPUT_HOOKS = [inject_session_state_prompt]
