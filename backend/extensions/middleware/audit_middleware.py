from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class SampleAuditMiddleware(AgentMiddleware):
    """Minimal pass-through middleware compatible with the current agent stack."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)


MIDDLEWARE = [SampleAuditMiddleware()]
