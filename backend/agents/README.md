# Agents

`backend/agents/` is the default recursive agent package.

The root package exports `AGENT` for the main assistant. Nested agents live in
`subagents/` and use the same shape:

```text
prompts/    system prompts
tools/      LangChain tools
skills/     DeepAgents skills
middleware/ LangChain/DeepAgents middleware
hooks/      run-input and upload hooks
memory/     markdown memory files
subagents/  nested agents
```

## Main Agent

```python
from pathlib import Path

from agents.hooks import RUN_INPUT_HOOKS, UPLOAD_HOOKS
from agents.memory import MEMORY
from agents.middleware import MIDDLEWARE
from agents.skills import SKILLS
from agents.subagents import SUBAGENTS
from agents.tools import TOOLS

ROOT = Path(__file__).parent

AGENT = {
    "id": "main",
    "name": "deepagents-web",
    "model": None,
    "workspace": "/workspace/main",
    "system_prompt": ROOT / "prompts" / "system.md",
    "tools": TOOLS,
    "skills": SKILLS,
    "middleware": MIDDLEWARE,
    "hooks": {
        "run_input": RUN_INPUT_HOOKS,
        "upload": UPLOAD_HOOKS,
    },
    "memory": MEMORY,
    "subagents": SUBAGENTS,
}
```

`model=None` means the agent uses `DEEPAGENTS_DEFAULT_MODEL`.

## Selection Patterns

Use `"*"` for every item in a directory:

```python
SKILLS = "*"
MEMORY = "*"
SUBAGENTS = "*"
```

Use one name for a single item:

```python
SKILLS = "skill-creator"
MEMORY = "project"
SUBAGENTS = "code_reviewer"
```

Use a list for multiple items:

```python
SKILLS = ["skill-creator", "review-checklist"]
MEMORY = ["project", "review"]
SUBAGENTS = ["code_reviewer"]
```

Use named groups when you want reusable presets:

```python
SKILL_SETS = {
    "default": ["skill-creator"],
    "review": ["skill-creator", "review-checklist"],
}

SKILLS = SKILL_SETS["review"]
```

## Sandbox

Sandbox/backend is configured globally in `.env`. Agents do not define their own
sandbox. Use `workspace` and `permissions` to isolate or share directories.

