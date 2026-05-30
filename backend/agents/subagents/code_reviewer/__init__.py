from __future__ import annotations

from pathlib import Path

from agents.subagents.code_reviewer.memory import MEMORY
from agents.subagents.code_reviewer.middleware import MIDDLEWARE
from agents.subagents.code_reviewer.skills import SKILLS
from agents.subagents.code_reviewer.tools import TOOLS

ROOT = Path(__file__).parent

SUBAGENT = {
    "id": "code-reviewer",
    "name": "code-reviewer",
    "label": "Code reviewer",
    "description": "Review implementation changes for defects, regressions, and missing tests.",
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

__all__ = ["SUBAGENT"]
