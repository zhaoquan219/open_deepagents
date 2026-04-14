from __future__ import annotations

import json
from typing import Any


def format_log_fields(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
