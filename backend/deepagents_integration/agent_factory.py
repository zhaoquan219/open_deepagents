from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent

from .config import DeepAgentsRuntimeConfig
from .extensions import (
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

    return create_deep_agent(
        model=config.model,
        tools=load_tool_extensions(config.tool_specs),
        middleware=load_middleware_extensions(config.middleware_specs),
        skills=list(active_skill_sources) or None,
        memory=list(config.memory) or None,
        permissions=build_permissions(config.permissions),
        system_prompt=config.system_prompt,
        backend=backend,
        debug=config.debug,
        name=config.agent_name,
    )
