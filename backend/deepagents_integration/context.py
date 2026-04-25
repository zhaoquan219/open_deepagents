from __future__ import annotations

from typing import Any, TypedDict


class DeepAgentsRunContext(TypedDict, total=False):
    session_id: str
    run_id: str
    timezone: str
    current_datetime: str
    current_date: str
    current_attachments: tuple[dict[str, Any], ...]
    attachments: tuple[dict[str, Any], ...]
    prompt_injections: dict[str, list[str]]
