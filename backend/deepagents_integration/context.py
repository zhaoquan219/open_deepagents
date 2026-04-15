from __future__ import annotations

from typing import Any, TypedDict


class DeepAgentsRunContext(TypedDict, total=False):
    session_id: str
    run_id: str
    current_attachments: tuple[dict[str, Any], ...]
    attachments: tuple[dict[str, Any], ...]
