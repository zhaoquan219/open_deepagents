from __future__ import annotations

import re
from pathlib import Path

WINDOWS_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def is_absolute_path(value: object) -> bool:
    text = str(value)
    return Path(text).is_absolute() or bool(WINDOWS_ABSOLUTE_RE.match(text))


def app_path(value: object, base: Path) -> Path:
    path = Path(str(value))
    return path if is_absolute_path(value) else base / path


def split_import_spec(spec: str) -> tuple[str, str]:
    module_name, separator, attr = spec.rpartition(":")
    if not separator or not module_name or not attr or _split_at_windows_drive(module_name, attr):
        raise ValueError(f"Import spec must be module:attribute: {spec}")
    return module_name, attr


def is_import_spec(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        split_import_spec(value)
    except ValueError:
        return False
    return True


def _split_at_windows_drive(module_name: str, attr: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]", module_name) and attr.startswith(("\\", "/")))
