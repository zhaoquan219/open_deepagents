# Sandbox Guide

The sandbox configuration controls where DeepAgents file tools read and write,
and whether shell execution is available. Choose the narrowest backend that fits
your deployment.

## Backend Kinds

| Kind | Best for | Behavior |
| --- | --- | --- |
| `state` | Default chat and file-processing runs. | Virtual in-memory file state. No host shell. |
| `filesystem` | File tools over a controlled directory. | Reads/writes through DeepAgents file tools under a configured root. |
| `local_shell` | Trusted local automation. | File tools plus host command execution through `execute`. |
| custom spec | External isolation service or custom policy. | Set `DEEPAGENTS_BACKEND_SPEC`. |

`local_shell` is not process isolation. Only enable it for trusted users and
pair it with strict built-in tool filtering and permissions.

## Recommended Defaults

Safe default:

```dotenv
DEEPAGENTS_SANDBOX_KIND=state
```

Filesystem backend rooted under backend data:

```dotenv
DEEPAGENTS_SANDBOX_KIND=filesystem
DEEPAGENTS_SANDBOX_ROOT_DIR=./agents
DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true
```

Trusted local shell:

```dotenv
DEEPAGENTS_SANDBOX_KIND=local_shell
DEEPAGENTS_SANDBOX_ROOT_DIR=./agents
DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true
```

Custom backend:

```dotenv
DEEPAGENTS_SANDBOX_KIND=custom
DEEPAGENTS_BACKEND_SPEC=path/to/custom_sandbox.py:build_backend
```

Custom specs can point to an importable module or a Python file path.

## Virtual Paths

For `filesystem` and `local_shell`, the app uses virtual path semantics. Inside
DeepAgents file tools, `/` maps to `DEEPAGENTS_SANDBOX_ROOT_DIR`, not to the host
filesystem root.

Example:

```dotenv
DEEPAGENTS_SANDBOX_ROOT_DIR=./agents
```

A tool path like `/workspace/project/file.txt` maps to:

```text
backend/data/workspace/project/file.txt
```

This path model keeps prompts and tools stable across macOS, Linux, and Windows.

## Files

The current backend API does not include upload/download storage or
generated-file export. If you add those features, keep stored files inside the
configured sandbox root when models need file-tool access, and pass model-facing
virtual paths in the event/message metadata.

Large or binary runtime payloads are summarized or redacted in event storage so
logs and UI timelines remain responsive.

## Built-in Tools and Permissions

Sandbox safety uses two layers.

Tool visibility:

```python
"builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
```

Path permissions:

```python
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
]
```

`read` covers `ls`, `read_file`, `glob`, and `grep`.
`write` covers `write_file` and `edit_file`.

Both layers must allow an action. If a tool is hidden, the model cannot call it.
If a visible tool targets an unpermitted path, the call is denied.

When permission specs are configured, unmatched read/write paths are denied.

## Practical Profiles

### Read-only assistant

```python
"builtin_tools": ("write_todos", "ls", "read_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute", "write_file", "edit_file"),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
],
```

### Controlled writer

```python
"builtin_tools": ("write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute",),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
],
```

### Trusted local shell

```python
"builtin_tools": ("write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "task"),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
],
```

Only use this profile when the deployment boundary already trusts the operator.

## Checklist Before Enabling Host Access

- Keep `ADMIN_AUTH_ENABLED=true` unless the deployment is trusted local only.
- Use long random admin passwords and token secrets.
- Hide `execute` unless command execution is required.
- Hide `write_file` and `edit_file` unless writes are required.
- Add explicit read/write permissions for the smallest useful paths.
- Prefer `state` for general chat.
