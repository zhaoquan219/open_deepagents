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

DEFAULT_BUILTIN_TOOLS = (
    "write_todos",
    "ls",
    "read_file",
    "glob",
    "grep",
    "task",
    "execute",
)

AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "workspace": "/workspace/main",
    "tools": TOOLS,
    "builtin_tools": DEFAULT_BUILTIN_TOOLS,
    "disabled_builtin_tools": ("write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
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
    "builtin_tools": ("ls", "read_file", "glob", "grep", "task"),
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "skills": ["skill-creator"],
    "memory": ["project"],
    "subagents": ["code_reviewer"],
    # Optional:
    "model": "openai/gpt-5-4",
    "workspace": "/workspace/main",
    "permissions": [
        {"operations": ["read"], "paths": ["/workspace/main"]},
    ],
}
```

- `model` 不写时，使用当前默认模型。
- `workspace` 只是给 UI/日志/作者看的描述字段，不是权限边界。
- `permissions` 才是文件读写边界。
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
    "workspace": "/workspace/reviews",
    "tools": TOOLS,
    "builtin_tools": DEFAULT_BUILTIN_TOOLS,
    "disabled_builtin_tools": ("write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    "permissions": [
        {"operations": ["read"], "paths": ["/workspace/reviews", "/workspace/shared"]},
    ],
}
```

Subagents are resolved recursively. Nested subagents can define their own prompt,
model, tools, built-in tool filter, permissions, skills, memory, middleware, and
children.
`workspace` is descriptive metadata for UI/logging and agent authoring. It does
not narrow filesystem or tool access by itself; use sandbox backend selection and
`permissions` for enforcement.

## Selection Patterns

Selection files such as `skills/__init__.py`, `memory/__init__.py`, and
`subagents/__init__.py` can export one item, a list, named presets, or `"*"`.

For the default scaffold, keep them simple:

```python
SKILLS = ["skill-creator"]
MEMORY = ["project"]
SUBAGENTS = ["code_reviewer"]
```

Select everything in a directory:

```python
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

These are different from DeepAgents built-in tools. Custom tools are passed
through by the built-in tool filter.

## Built-in Tool Visibility

Use `builtin_tools` to expose only the DeepAgents built-ins you want the model to
see. Use `disabled_builtin_tools` to hide specific built-ins. If a built-in
appears in both lists, `disabled_builtin_tools` wins.

When no subagents are active, the backend automatically hides the built-in
`task` tool even if it appears in `builtin_tools`.

```python
"builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
```

Useful built-in names include:

- `write_todos`
- `ls`
- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `grep`
- `execute`
- `task`

## Permissions

Permissions are path authorization for visible file tools.

```python
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
]
```

`operations=["read"]` covers `ls`, `read_file`, `glob`, and `grep`.
`operations=["write"]` covers `write_file` and `edit_file`.

Tool visibility and permissions must both allow an action:

- If `read_file` is hidden, the model cannot call it.
- If `read_file` is visible but the path is not permitted, the call is blocked.
- If a path is writable but `write_file` and `edit_file` are hidden, the model
  still cannot write through those built-ins.

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

## Skills and Memory

Skills are exposed through DeepAgents skill paths. Memory files are Markdown
documents selected by `memory/__init__.py`.

Use skills for procedural instructions and tools; use memory for durable project
facts, policies, terminology, and reviewer context.

## Sandbox Boundary

Sandbox backend selection is global and configured through `backend/.env`.
Agents and subagents do not define their own sandbox backend. They can define
`workspace` as metadata, built-in tool visibility to shape model-visible tools,
and permissions to enforce file access inside the global backend.
