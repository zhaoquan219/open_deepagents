from __future__ import annotations

from pathlib import Path

from agents.hooks import RUN_INPUT_HOOKS, UPLOAD_HOOKS
from agents.memory import MEMORY
from agents.middleware import MIDDLEWARE
from agents.skills import SKILLS
from agents.subagents import SUBAGENTS
from agents.tools import TOOLS

ROOT = Path(__file__).parent
READ_ONLY_BUILTIN_TOOLS = (
    "write_todos",
    "ls",
    "read_file",
    "glob",
    "grep",
    "task",
)

AGENT = {
    "id": "main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "workspace": "/workspace/main",
    "tools": TOOLS,
    # Visible DeepAgents built-ins. `task` is auto-hidden when no subagents are active.
    "builtin_tools": READ_ONLY_BUILTIN_TOOLS,
    "disabled_builtin_tools": ("execute", "write_file", "edit_file"),
    "middleware": MIDDLEWARE,
    "hooks": {
        "run_input": RUN_INPUT_HOOKS,
        "upload": UPLOAD_HOOKS,
    },
    "skills": SKILLS,
    "memory": MEMORY,
    # Optional:
    # "model": "openai/gpt-5-4",
    # "permissions": [{"operations": ["read"], "paths": ["/workspace/main"]}],
    # Start with explicit children.
    "subagents": SUBAGENTS,
}

__all__ = ["AGENT"]
