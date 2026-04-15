from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent

from .config import DeepAgentsRuntimeConfig
from .context import DeepAgentsRunContext
from .extensions import (
    build_builtin_tool_selection_middleware,
    build_permissions,
    load_middleware_extensions,
    load_tool_extensions,
    resolve_backend,
    route_skill_sources,
)


def build_deep_agent(config: DeepAgentsRuntimeConfig) -> Any:
    """Create a compiled DeepAgents graph from thin app-layer config."""

    backend = resolve_backend(config.sandbox)
    backend, configured_skill_sources = route_skill_sources(backend, config.skill_sources)
    active_skill_sources = configured_skill_sources if config.skill_sources else config.skills
    middleware = load_middleware_extensions(config.middleware_specs)
    tool_selection = build_builtin_tool_selection_middleware(
        allowlist=config.builtin_tool_allowlist,
        blocklist=config.builtin_tool_blocklist,
    )
    if tool_selection is not None:
        middleware.append(tool_selection)

    return create_deep_agent(
        model=config.model,
        tools=load_tool_extensions(config.tool_specs),
        middleware=middleware,
        skills=list(active_skill_sources) or None,
        memory=list(config.memory) or None,
        permissions=build_permissions(config.permissions),
        system_prompt=config.system_prompt,
        context_schema=DeepAgentsRunContext,
        backend=backend,
        debug=config.debug,
        name=config.agent_name,
    )
