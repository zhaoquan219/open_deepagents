from __future__ import annotations

from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent

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
    middleware = [*config.middleware, *load_middleware_extensions(config.middleware_specs)]
    tool_selection = build_builtin_tool_selection_middleware(
        allowlist=config.builtin_tool_allowlist,
        blocklist=config.builtin_tool_blocklist,
    )
    if tool_selection is not None:
        middleware.append(tool_selection)

    return create_deep_agent(
        model=config.model,
        tools=[*config.tools, *load_tool_extensions(config.tool_specs)],
        middleware=middleware,
        skills=list(active_skill_sources) or None,
        memory=list(config.memory) or None,
        permissions=build_permissions(config.permissions),
        subagents=_deepagents_subagents(config.subagents),
        system_prompt=config.system_prompt,
        context_schema=DeepAgentsRunContext,
        checkpointer=config.checkpointer,
        store=config.store,
        backend=backend,
        interrupt_on=dict(config.interrupt_on) if config.interrupt_on else None,
        debug=config.debug,
        name=config.agent_name,
        cache=config.cache,
    )


def _deepagents_subagents(
    subagents: tuple[dict[str, Any] | Any, ...] | tuple[Any, ...],
) -> list[SubAgent | CompiledSubAgent | AsyncSubAgent] | None:
    if not subagents:
        return None
    converted: list[SubAgent | CompiledSubAgent | AsyncSubAgent] = []
    for item in subagents:
        if not isinstance(item, dict):
            converted.append(cast("SubAgent | CompiledSubAgent | AsyncSubAgent", item))
            continue
        if "graph_id" in item:
            allowed = {"name", "description", "graph_id", "url", "headers"}
        elif "runnable" in item:
            allowed = {"name", "description", "runnable"}
        else:
            item = _with_builtin_tool_selection_middleware(item)
            allowed = {
                "name",
                "description",
                "system_prompt",
                "tools",
                "model",
                "middleware",
                "interrupt_on",
                "skills",
                "permissions",
                "subagents",
            }
        converted.append(
            cast(
                "SubAgent | CompiledSubAgent | AsyncSubAgent",
                {key: value for key, value in item.items() if key in allowed},
            )
        )
    return converted


def _with_builtin_tool_selection_middleware(item: dict[str, Any]) -> dict[str, Any]:
    tool_selection = build_builtin_tool_selection_middleware(
        allowlist=item.get("builtin_tool_allowlist") or item.get("builtin_tools"),
        blocklist=item.get("builtin_tool_blocklist") or item.get("disabled_builtin_tools"),
    )
    if tool_selection is None:
        return item
    return {**item, "middleware": [*item.get("middleware", ()), tool_selection]}
