from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware, AgentState, ToolCallRequest, before_agent
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from deepagents_integration.context import DeepAgentsRunContext

logger = logging.getLogger(__name__)
ToolCallResult = ToolMessage | Command[object]
ToolCallHandler = Callable[[ToolCallRequest], Any]


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


def _log_tool_call(request: ToolCallRequest) -> None:
    context: Mapping[str, Any] = request.runtime.context or {}
    logger.debug(
        "DeepAgents tool call received",
        extra={
            "tool_name": str(request.tool_call.get("name") or ""),
            "session_id": context.get("session_id", ""),
            "attachment_count": len(context.get("current_attachments") or ()),
        },
    )


class AuditAttachmentToolCall(AgentMiddleware[Any, Any]):
    """Tool middleware with sync and async entrypoints for test/runtime parity."""

    name = "AuditAttachmentToolCall"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolCallHandler,
    ) -> ToolCallResult:
        _log_tool_call(request)
        response = handler(request)
        if inspect.isawaitable(response):
            raise RuntimeError("Synchronous tool middleware received an async handler")
        return cast(ToolCallResult, response)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolCallHandler,
    ) -> ToolCallResult:
        _log_tool_call(request)
        response = handler(request)
        if inspect.isawaitable(response):
            return await cast(Awaitable[ToolCallResult], response)
        return cast(ToolCallResult, response)


MIDDLEWARE = [log_run_context, AuditAttachmentToolCall()]
