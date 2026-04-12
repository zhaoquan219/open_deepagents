from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


SandboxKind = Literal["state", "filesystem", "local_shell", "custom"]


@dataclass(frozen=True)
class SandboxConfig:
    """Runtime backend configuration for DeepAgents.

    The scaffold keeps backend selection intentionally thin and forwards the
    resolved backend directly to ``deepagents.create_deep_agent``.
    """

    kind: SandboxKind = "state"
    root_dir: str | None = None
    virtual_mode: bool | None = None
    timeout: int = 120
    max_output_bytes: int = 100_000
    inherit_env: bool = False
    env: dict[str, str] = field(default_factory=dict)
    backend_spec: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "SandboxConfig":
        if raw is None:
            return cls()
        kind = raw.get("kind", "state")
        if kind not in {"state", "filesystem", "local_shell", "custom"}:
            raise ValueError(f"Unsupported sandbox kind: {kind!r}")
        env_raw = raw.get("env", {})
        if env_raw is None:
            env_raw = {}
        if not isinstance(env_raw, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env_raw.items()
        ):
            raise ValueError("sandbox.env must be a mapping of string keys and string values")
        config = cls(
            kind=kind,
            root_dir=raw.get("root_dir"),
            virtual_mode=raw.get("virtual_mode"),
            timeout=int(raw.get("timeout", 120)),
            max_output_bytes=int(raw.get("max_output_bytes", 100_000)),
            inherit_env=bool(raw.get("inherit_env", False)),
            env=dict(env_raw),
            backend_spec=raw.get("backend_spec"),
        )
        if config.kind == "custom" and not config.backend_spec:
            raise ValueError("sandbox.backend_spec is required when sandbox.kind='custom'")
        return config


@dataclass(frozen=True)
class DeepAgentsRuntimeConfig:
    """Application-facing configuration for the DeepAgents runtime adapter."""

    model: Any = None
    system_prompt: str | None = None
    agent_name: str | None = None
    debug: bool = False
    tool_specs: tuple[str, ...] = ()
    middleware_specs: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    permissions: tuple[Mapping[str, Any], ...] = ()
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DeepAgentsRuntimeConfig":
        return cls(
            model=raw.get("model"),
            system_prompt=_optional_str(raw.get("system_prompt")),
            agent_name=_optional_str(raw.get("agent_name")),
            debug=bool(raw.get("debug", False)),
            tool_specs=_string_tuple(raw.get("tool_specs")),
            middleware_specs=_string_tuple(raw.get("middleware_specs")),
            skills=_string_tuple(raw.get("skills")),
            memory=_string_tuple(raw.get("memory")),
            permissions=_mapping_tuple(raw.get("permissions")),
            sandbox=SandboxConfig.from_mapping(raw.get("sandbox")),
        )


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string or null, got: {type(value).__name__}")
    return value


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError("Expected a list of strings")
    return tuple(value)


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("Expected a list of mappings")
    return tuple(value)
