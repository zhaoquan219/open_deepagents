from __future__ import annotations

import json
from typing import Any


def format_log_fields(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)


def format_log_message(summary: str, **fields: Any) -> str:
    return f"{summary} {format_log_fields(summary=summary, **fields)}"
