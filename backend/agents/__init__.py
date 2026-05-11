from __future__ import annotations

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
    # Optional:
    # "model": "openai/gpt-5-4",
    "permissions": [
        {"builtin_tools": ("write_todos", "task", "execute")},
        {
            "builtin_tools": ("ls", "read_file", "glob", "grep"),
            "paths": ["/workspace/main", "/skills", "/uploads"],
        },
    ],
    # Start with explicit children.
    "subagents": SUBAGENTS,
}

__all__ = ["AGENT"]
