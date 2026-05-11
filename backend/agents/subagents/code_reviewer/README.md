# Code Reviewer Subagent

`code_reviewer` is an example recursive subagent package. It is configured for
read-only review work and can be used as a template for adding more subagents.

## Package Layout

```text
code_reviewer/
├── __init__.py                  `SUBAGENT` mapping
├── prompts/system.md            Reviewer role prompt
├── tools/__init__.py            Reviewer-specific tools
├── middleware/__init__.py       Reviewer middleware
├── skills/__init__.py           Selected reviewer skills
├── skills/review-checklist/     Local review checklist skill
├── memory/__init__.py           Selected reviewer memory
├── memory/review.md             Durable review guidance
└── subagents/__init__.py        Optional nested subagents
```

## Configuration Shape

```python
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
        {"builtin_tools": ("write_todos", "task")},
        {
            "builtin_tools": ("ls", "read_file", "glob", "grep"),
            "paths": ["/workspace/reviews", "/workspace/shared"],
        },
    ],
}
```

## Behavior

- The subagent uses the default selected model unless `model` is added explicitly.
- The system prompt tells it to lead with concrete findings.
- Built-in write and shell tools are omitted from `permissions[].builtin_tools`.
- File permissions allow read access only to review and shared workspace paths.
- Local skills and memory provide review checklists and durable review guidance.

## Customizing

- Edit `prompts/system.md` to change the reviewer role.
- Add tools under `tools/` and export them from `tools/__init__.py`.
- Add or remove reviewer skills in `skills/__init__.py`.
- Add durable review facts in `memory/`.
- Add nested reviewer helpers under `subagents/` when the review task benefits
  from more specialized child agents.
