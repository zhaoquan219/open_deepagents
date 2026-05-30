# Agent Package Guide

`backend/agents/` is the default recursive agent package used by the backend.
It lets you change agent behavior by editing Python package exports and Markdown
resources instead of changing the FastAPI application.

## Package Layout

```text
backend/agents/
├── __init__.py                  Main `AGENT` mapping
├── prompts/system.md            Main system prompt
├── tools/__init__.py            Custom tools
├── middleware/__init__.py       Runtime middleware
├── skills/__init__.py           Selected DeepAgents skills
├── memory/__init__.py           Selected memory files
├── memory/*.md                  Markdown memory resources
└── subagents/                   Recursive subagent packages
```

Every subagent package can use the same pattern:

```text
subagents/<name>/
├── __init__.py
├── prompts/system.md
├── tools/__init__.py
├── middleware/__init__.py
├── skills/__init__.py
├── memory/__init__.py
└── subagents/__init__.py
```

## Smallest Useful Main Agent

```python
from pathlib import Path

from agents.memory import MEMORY
from agents.middleware import MIDDLEWARE
from agents.skills import SKILLS
from agents.subagents import SUBAGENTS
from agents.tools import TOOLS

ROOT = Path(__file__).parent

AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "permissions": [
        {
            "operations": ("read",),
            "paths": ["/workspace/main", "/skills", "/uploads"],
        },
        {"operations": ("write",), "paths": ["/workspace/main/output"]},
    ],
    "subagents": SUBAGENTS,
}
```

这是推荐起点。先只保留你真的会改的字段，再按需加可选项。

## Optional Agent Fields

这些字段仍然支持，但现在建议按需加，不要默认全堆在示例里：

```python
AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "skills": ["skill-creator"],
    "memory": ["project"],
    "subagents": ["code_reviewer"],
    # Optional:
    "model": "openai/gpt-5-4",
    "permissions": [
        {"operations": ("read",), "paths": ["/workspace/main", "/skills", "/uploads"]},
        {"operations": ("write",), "paths": ["/workspace/main/output"]},
    ],
}
```

- `model` 不写时，使用当前默认模型。
- `permissions[].operations` 使用 DeepAgents 原生文件权限，控制虚拟路径读写。
- `skills` / `memory` / `subagents` 直接写列表，默认最容易看懂。
- 这些选择会相对于当前 agent package 解析，而不是错误地从 `backend/` 根目录拼接。

## Subagent Shape

```python
SUBAGENT = {
    "id": "code-reviewer",
    "name": "code-reviewer",
    "label": "Code reviewer",
    "description": "Review implementation changes for defects.",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "permissions": [
        {
            "operations": ("read",),
            "paths": ["/workspace/reviews", "/workspace/shared", "/skills", "/uploads"],
        },
    ],
}
```

Subagents are resolved recursively. Nested subagents can define their own prompt,
model, tools, permissions, skills, memory, middleware, and children.
Use sandbox backend selection and `permissions` for filesystem enforcement.

## Selection Patterns

Selection files such as `tools/__init__.py`, `middleware/__init__.py`,
`skills/__init__.py`, `memory/__init__.py`, and `subagents/__init__.py` can
export one item, a list, named presets, or `"*"`.

For the default scaffold, keep them simple:

```python
SKILLS = ["skill-creator"]
MEMORY = ["project"]
SUBAGENTS = ["code_reviewer"]
```

Select everything in a directory:

```python
TOOLS = "*"
MIDDLEWARE = "*"
SKILLS = "*"
MEMORY = "*"
SUBAGENTS = "*"
```

Select one item:

```python
SKILLS = "skill-creator"
MEMORY = "project"
SUBAGENTS = "code_reviewer"
```

Select several items:

```python
SKILLS = ["skill-creator", "review-checklist"]
MEMORY = ["project", "review"]
SUBAGENTS = ["code_reviewer", "researcher"]
```

Use named presets:

```python
SKILL_SETS = {
    "default": ["skill-creator"],
    "review": ["skill-creator", "review-checklist"],
}

SKILLS = SKILL_SETS["review"]
```

## Tools

Custom tools are exported from `tools/__init__.py` through `TOOLS`.

```python
from agents.tools.search import search_docs

TOOLS = [search_docs]
```

These are different from DeepAgents built-in tools. Custom tools are passed to
the DeepAgents runtime unchanged.

## Permissions

Permissions are native DeepAgents filesystem permissions. They authorize file
operations against virtual sandbox paths. Built-in tool visibility is not
configured by this scaffold.

```python
"permissions": [
    {"operations": ("read",), "paths": ["/workspace/main", "/skills", "/uploads"]},
    {"operations": ("write",), "paths": ["/workspace/main/output"]},
]
```

`read` covers filesystem reads such as `ls`, `read_file`, `glob`, and `grep`
when the runtime exposes those tools. `write` covers filesystem writes such as
`write_file` and `edit_file` when available.

The permissions surface must allow an action:

- If a path is readable, file read operations may read it.
- If a path is writable, file write operations may write it.
- If a path is not matched, the backend appends deny rules for read and write.

## Middleware-First Customization

不要再维护单独的 hooks 目录。运行时输入增强、上下文查看、工具调用审计、
以及提示词补充都统一放在 middleware。

```python
from langchain.agents.middleware import before_agent
from langgraph.runtime import Runtime

@before_agent(name="ReadSessionContext")
async def read_session_context(state, runtime: Runtime[object]) -> None:
    del state
    context = runtime.context or {}
    session_id = context.get("session_id", "")
    metadata = context.get("session_metadata", {})
    _ = (session_id, metadata)
```

需要的上下文字段直接从 runtime context 读取，不再额外配置 hooks。

上传文件也是同一路径：后端把文件信息放入
`runtime.context.current_attachments`，不直接改写用户 prompt。如果确实要把上传
文件路径注入成额外 message，可以在 middleware 中显式选择：

```python
from agents.middleware.audit_middleware import (
    AuditAttachmentToolCall,
    inject_attachment_context_message,
    log_run_context,
)

MIDDLEWARE = [
    log_run_context,
    inject_attachment_context_message,
    AuditAttachmentToolCall(),
]
```

## Skills and Memory

Skills are exposed through DeepAgents skill paths. Memory files are Markdown
documents selected by `memory/__init__.py`.

Use skills for procedural instructions and tools; use memory for durable project
facts, policies, terminology, and reviewer context.

## Sandbox Boundary

Sandbox backend selection is global and configured through `backend/.env`.
Agents and subagents do not define their own sandbox backend. They can define
permissions to enforce file access inside the global backend.
