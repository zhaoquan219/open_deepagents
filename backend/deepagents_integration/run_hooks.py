from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .extensions import load_object_from_spec


@dataclass(frozen=True)
class RunInputHookContext:
    session_id: str
    run_id: str
    role: str
    content: str
    attachments: tuple[Mapping[str, Any], ...] = ()
    is_current_run: bool = False


@dataclass(frozen=True)
class UploadHookContext:
    upload_id: str
    session_id: str
    message_id: str | None
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    sha256: str
    upload_path: str
    payload: bytes


def apply_run_input_hooks(
    *,
    context: RunInputHookContext,
    hook_specs: tuple[str, ...] = (),
) -> str:
    hooks = _load_hooks(hook_specs) if hook_specs else ()
    content = context.content
    for hook in hooks:
        result = hook(replace(context, content=content))
        if result is None:
            continue
        content = _coerce_hook_content(result)
    return content


def apply_upload_hooks(
    *,
    context: UploadHookContext,
    hook_specs: tuple[str, ...] = (),
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for hook in _load_hooks(hook_specs):
        result = hook(context)
        if result is None:
            continue
        if not isinstance(result, Mapping):
            raise ValueError("Upload hooks must return a mapping or None")
        extra.update(dict(result))
    return extra


def build_upload_hook_context(
    *,
    upload_id: str,
    session_id: str,
    message_id: str | None,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    sha256: str,
    upload_root: Path,
    payload: bytes,
) -> UploadHookContext:
    return UploadHookContext(
        upload_id=upload_id,
        session_id=session_id,
        message_id=message_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        sha256=sha256,
        upload_path=str((upload_root / storage_key).resolve()),
        payload=payload,
    )


def _load_hooks(specs: tuple[str, ...]) -> tuple[Any, ...]:
    hooks: list[Any] = []
    for spec in specs:
        loaded = load_object_from_spec(spec)
        if isinstance(loaded, list | tuple):
            hooks.extend(loaded)
        else:
            hooks.append(loaded)
    return tuple(hooks)


def _coerce_hook_content(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping) and "content" in result:
        return str(result["content"])
    raise ValueError("Run input hooks must return a string, a mapping with content, or None")
