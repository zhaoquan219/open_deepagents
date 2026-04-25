from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROMPT_INJECTION_SLOTS = (
    "system_prefix",
    "system_suffix",
    "user_prefix",
    "user_suffix",
    "current_user_prefix",
    "current_user_suffix",
)


class InvalidRuntimeOverrideError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeOverrides:
    timezone: str
    now: datetime
    prompt_injections: dict[str, tuple[str, ...]]

    @property
    def current_datetime(self) -> str:
        return self.now.isoformat()

    @property
    def current_date(self) -> str:
        return self.now.date().isoformat()

    def context_fields(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "current_datetime": self.current_datetime,
            "current_date": self.current_date,
            "prompt_injections": {
                key: list(value) for key, value in self.prompt_injections.items() if value
            },
        }


def validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise InvalidRuntimeOverrideError(f"Unsupported timezone: {value!r}") from exc
    return value


def normalize_persisted_extra(extra: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(extra or {})
    runtime = normalized.get("runtime")
    if runtime is None:
        return normalized
    normalized_runtime = normalize_runtime_payload(runtime)
    if normalized_runtime:
        normalized["runtime"] = normalized_runtime
    else:
        normalized.pop("runtime", None)
    return normalized


def resolve_runtime_overrides(
    *,
    default_timezone: str,
    session_extra: dict[str, Any] | None,
    run_extra: dict[str, Any] | None,
) -> RuntimeOverrides:
    timezone = default_timezone
    merged: dict[str, tuple[str, ...]] = {slot: () for slot in PROMPT_INJECTION_SLOTS}
    for extra in (session_extra, run_extra):
        runtime = runtime_payload(extra)
        if not runtime:
            continue
        timezone = str(runtime.get("timezone") or timezone)
        prompt_injections = runtime.get("prompt_injections") or {}
        if isinstance(prompt_injections, dict):
            for slot in PROMPT_INJECTION_SLOTS:
                values = _normalize_injection_values(prompt_injections.get(slot))
                if values:
                    merged[slot] = (*merged[slot], *values)
    timezone = validate_timezone_name(timezone)
    return RuntimeOverrides(
        timezone=timezone,
        now=datetime.now(ZoneInfo(timezone)),
        prompt_injections={slot: values for slot, values in merged.items() if values},
    )


def apply_system_prompt_overrides(system_prompt: str | None, overrides: RuntimeOverrides) -> str:
    base_prompt = (system_prompt or "").strip()
    parts = [
        _render_injection_block(
            "Runtime System Prefix",
            overrides.prompt_injections.get("system_prefix", ()),
        ),
        base_prompt,
        _render_timezone_block(overrides),
        _render_injection_block(
            "Runtime System Suffix",
            overrides.prompt_injections.get("system_suffix", ()),
        ),
    ]
    return "\n\n".join(part for part in parts if part)


def apply_user_prompt_overrides(
    content: str,
    *,
    overrides: RuntimeOverrides,
    is_current: bool,
) -> str:
    sections = [
        _render_injection_block(
            "Runtime User Prefix",
            (
                *overrides.prompt_injections.get("user_prefix", ()),
                *(
                    overrides.prompt_injections.get("current_user_prefix", ())
                    if is_current
                    else ()
                ),
            ),
        ),
        content.strip(),
        _render_injection_block(
            "Runtime User Suffix",
            (
                *overrides.prompt_injections.get("user_suffix", ()),
                *(
                    overrides.prompt_injections.get("current_user_suffix", ())
                    if is_current
                    else ()
                ),
            ),
        ),
    ]
    return "\n\n".join(part for part in sections if part)


def runtime_payload(extra: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(extra, dict):
        return {}
    runtime = extra.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def normalize_runtime_payload(raw: Any) -> dict[str, Any]:
    if raw in ({}, None):
        return {}
    if not isinstance(raw, dict):
        raise InvalidRuntimeOverrideError("extra.runtime must be a mapping")
    normalized: dict[str, Any] = {}
    timezone = raw.get("timezone")
    if timezone is not None:
        if not isinstance(timezone, str) or not timezone.strip():
            raise InvalidRuntimeOverrideError("extra.runtime.timezone must be a non-empty string")
        normalized["timezone"] = validate_timezone_name(timezone.strip())
    prompt_injections = raw.get("prompt_injections")
    if prompt_injections is not None:
        if not isinstance(prompt_injections, dict):
            raise InvalidRuntimeOverrideError(
                "extra.runtime.prompt_injections must be a mapping"
            )
        normalized_injections = {
            slot: list(values)
            for slot, values in (
                (slot, _normalize_injection_values(prompt_injections.get(slot)))
                for slot in PROMPT_INJECTION_SLOTS
            )
            if values
        }
        if normalized_injections:
            normalized["prompt_injections"] = normalized_injections
    return normalized


def _normalize_injection_values(value: Any) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise InvalidRuntimeOverrideError(
            "prompt injection values must be a string or list of strings"
        )
    return tuple(item.strip() for item in value if item.strip())


def _render_injection_block(title: str, values: tuple[str, ...]) -> str:
    if not values:
        return ""
    lines = [f"- {value}" for value in values]
    return f"[{title}]\n" + "\n".join(lines)


def _render_timezone_block(overrides: RuntimeOverrides) -> str:
    return "\n".join(
        [
            "[Runtime Context]",
            f"- Timezone: {overrides.timezone}",
            f"- Current datetime: {overrides.current_datetime}",
            f"- Current date: {overrides.current_date}",
        ]
    )
