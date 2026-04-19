# Code Reviewer Subagent

This example subagent shows the recursive agent-package layout:

- `prompts/system.md` defines its system prompt.
- `tools/__init__.py` exports local tools.
- `skills/__init__.py` exports selectable skill sets.
- `middleware/__init__.py` exports local middleware.
- `hooks/__init__.py` exports optional hooks.
- `memory/__init__.py` exports selected markdown memory.
- `subagents/__init__.py` can expose nested subagents.

