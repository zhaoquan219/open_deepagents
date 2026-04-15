from __future__ import annotations

import logging
from collections.abc import Callable

from langchain.agents.middleware import AgentState, ToolCallRequest, before_agent, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from deepagents_integration.context import DeepAgentsRunContext

logger = logging.getLogger(__name__)


@before_agent(name="LogRunContext")
def log_run_context(
    state: AgentState[object],
    runtime: Runtime[DeepAgentsRunContext],
) -> None:
    """Function-style middleware example for run-scoped context access."""

    context = runtime.context or {}
    logger.debug(
        "DeepAgents run context received",
        extra={
            "run_id": context.get("run_id", ""),
            "session_id": context.get("session_id", ""),
            "current_attachment_count": len(context.get("current_attachments") or ()),
        },
    )


@wrap_tool_call(name="AuditAttachmentToolCall")
def audit_attachment_tool_call(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
) -> ToolMessage | Command[object]:
    """Function-style tool middleware; request.runtime.context is available here."""

    context = request.runtime.context or {}
    logger.debug(
        "DeepAgents tool call received",
        extra={
            "tool_name": str(request.tool_call.get("name") or ""),
            "run_id": context.get("run_id", ""),
            "session_id": context.get("session_id", ""),
            "current_attachment_count": len(context.get("current_attachments") or ()),
        },
    )
    return handler(request)


MIDDLEWARE = [log_run_context, audit_attachment_tool_call]
