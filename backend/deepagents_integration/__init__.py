"""Thin DeepAgents integration layer for the scaffold backend."""

from .agent_factory import build_deep_agent
from .config import DeepAgentsRuntimeConfig, SandboxConfig, SkillSourceConfig
from .extensions import (
    BuiltinToolSelectionMiddleware,
    build_builtin_tool_selection_middleware,
    load_middleware_extensions,
    load_object_from_spec,
    load_tool_extensions,
    resolve_backend,
    route_skill_sources,
)
from .run_hooks import (
    RunInputHookContext,
    UploadHookContext,
    apply_run_input_hooks,
    apply_upload_hooks,
    build_upload_hook_context,
    default_attachment_context_hook,
)
from .sse_bridge import (
    SSE_SCHEMA_VERSION,
    SseEventEnvelope,
    normalize_runtime_event,
    stream_sse_envelopes,
    validate_sse_event,
)

__all__ = [
    "DeepAgentsRuntimeConfig",
    "SSE_SCHEMA_VERSION",
    "SandboxConfig",
    "SkillSourceConfig",
    "SseEventEnvelope",
    "BuiltinToolSelectionMiddleware",
    "build_deep_agent",
    "build_builtin_tool_selection_middleware",
    "RunInputHookContext",
    "UploadHookContext",
    "apply_run_input_hooks",
    "apply_upload_hooks",
    "build_upload_hook_context",
    "default_attachment_context_hook",
    "load_middleware_extensions",
    "load_object_from_spec",
    "load_tool_extensions",
    "normalize_runtime_event",
    "route_skill_sources",
    "resolve_backend",
    "stream_sse_envelopes",
    "validate_sse_event",
]
