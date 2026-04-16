from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware import AgentState, ToolCallRequest, before_agent, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from deepagents_integration.context import DeepAgentsRunContext

logger = logging.getLogger(__name__)
ToolCallResult = ToolMessage | Command[object]
ToolCallHandler = Callable[[ToolCallRequest], ToolCallResult | Awaitable[ToolCallResult]]


@before_agent(name="LogRunContext")
async def log_run_context(
    state: AgentState[object],
    runtime: Runtime[DeepAgentsRunContext],
) -> None:
    """Async middleware example for run-scoped context access."""

    _ = state
    context = runtime.context or {}
    logger.debug(
        "DeepAgents run context received",
        extra={
            "session_id": context.get("session_id", ""),
            "attachment_count": len(context.get("current_attachments") or ()),
        },
    )


@wrap_tool_call(name="AuditAttachmentToolCall")
async def audit_attachment_tool_call(
    request: ToolCallRequest,
    handler: ToolCallHandler,
) -> ToolCallResult:
    """Function-style tool middleware for both sync and async handlers."""

    context = request.runtime.context or {}
    logger.debug(
        "DeepAgents tool call received",
        extra={
            "tool_name": str(request.tool_call.get("name") or ""),
            "session_id": context.get("session_id", ""),
            "attachment_count": len(context.get("current_attachments") or ()),
        },
    )
    response = handler(request)
    if inspect.isawaitable(response):
        return await cast(Awaitable[ToolCallResult], response)
    return cast(ToolCallResult, response)


MIDDLEWARE = [log_run_context, audit_attachment_tool_call]
