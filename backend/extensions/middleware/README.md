# Middleware Extensions

Middleware in this directory is loaded by the backend through:

```dotenv
DEEPAGENTS_MIDDLEWARE_SPECS=extensions.middleware:MIDDLEWARE
```

Use LangChain's function-style middleware decorators for project behavior. The
backend passes a typed DeepAgents runtime context into each run, so middleware
can read `session_id`, `run_id`, and `current_attachments`.

## Before-Agent Hook

```python
from langchain.agents.middleware import AgentState, before_agent
from langgraph.runtime import Runtime

from deepagents_integration.context import DeepAgentsRunContext


@before_agent
def inspect_run_context(
    state: AgentState[object],
    runtime: Runtime[DeepAgentsRunContext],
) -> None:
    context = runtime.context or {}
    session_id = context.get("session_id", "")
    attachments = context.get("current_attachments", ())
```

## Tool-Call Wrapper

```python
from collections.abc import Callable

from langchain.agents.middleware import ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import Command


@wrap_tool_call
def audit_tool_call(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
) -> ToolMessage | Command[object]:
    context = request.runtime.context or {}
    return handler(request)
```

Export middleware instances from `backend/extensions/middleware/__init__.py`:

```python
from extensions.middleware.audit_middleware import MIDDLEWARE as AUDIT_MIDDLEWARE

MIDDLEWARE = [*AUDIT_MIDDLEWARE]
```
