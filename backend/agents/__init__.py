from __future__ import annotations

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
    # Visible DeepAgents built-ins. `task` is auto-hidden when no subagents are active.
    "builtin_tools": DEFAULT_BUILTIN_TOOLS,
    "disabled_builtin_tools": ("write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "skills": SKILLS,
    "memory": MEMORY,
    # Optional:
    # "model": "openai/gpt-5-4",
    "permissions": [{"operations": ["read"], "paths": ["/workspace/main", "/skills"]}],
    # Start with explicit children.
    "subagents": SUBAGENTS,
}

__all__ = ["AGENT"]
