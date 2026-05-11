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
DEEPAGENTS_SANDBOX_PROFILE=safe
```

Filesystem backend rooted under backend data:

```dotenv
DEEPAGENTS_SANDBOX_PROFILE=files
```

Trusted local shell:

```dotenv
DEEPAGENTS_SANDBOX_PROFILE=shell
```

Custom backend:

```dotenv
DEEPAGENTS_SANDBOX_KIND=custom
DEEPAGENTS_BACKEND_SPEC=path/to/custom_sandbox.py:build_backend
```

Custom specs can point to an importable module or a Python file path.

## Virtual Paths

For `filesystem` and `local_shell`, the app uses virtual path semantics. Inside
DeepAgents file tools, `/` maps to the configured sandbox root, not to the host
filesystem root. The `files` and `shell` profiles default that root to
`backend/data/sandbox`.

Example:

```dotenv
DEEPAGENTS_SANDBOX_PROFILE=files
```

A tool path like `/workspace/project/file.txt` maps to:

```text
backend/data/sandbox/workspace/project/file.txt
```

This path model keeps prompts and tools stable across macOS, Linux, and Windows.

## Files

Session uploads are stored under `backend/data/uploads/{short_session_id}`
by default. Every sandbox profile mounts the upload root read-only at
`/uploads/...`, so uploaded files can be read by DeepAgents file tools without
widening the writable workspace root. Skill files are also mounted read-only at
`/skills/...`.

Run requests persist attachment metadata in the `user.message` event and pass
the same file information through `runtime.context.current_attachments`.
Download and delete operations are scoped to the authenticated owner. The
backend does not mutate the user's prompt; projects that want an extra
attachment-context message can add middleware in `backend/agents/middleware`.

Large or binary runtime payloads are summarized or redacted in event storage so
logs and UI timelines remain responsive.

## Built-in Tools and Permissions

Sandbox safety uses a single user-facing permission surface.

```python
"permissions": [
    {"builtin_tools": ("write_todos", "task")},
    {"builtin_tools": ("ls", "read_file", "glob", "grep"), "paths": ["/workspace/main"]},
    {"builtin_tools": ("write_file", "edit_file"), "paths": ["/workspace/main/output"]},
]
```

`ls`, `read_file`, `glob`, and `grep` map to read permissions. `write_file`
and `edit_file` map to write permissions. `"*"` in `builtin_tools` allows every
known built-in.

The permission entry must allow an action. If a tool is not listed, the model
cannot call it. If a listed file tool targets an unpermitted path, the call is
denied.

When permission specs are configured, unmatched read/write paths are denied.

## Practical Profiles

### Read-only assistant

```python
"permissions": [
    {"builtin_tools": ("write_todos", "task")},
    {"builtin_tools": ("ls", "read_file", "glob", "grep"), "paths": ["/workspace/main"]},
],
```

### Controlled writer

```python
"permissions": [
    {"builtin_tools": ("write_todos", "task")},
    {"builtin_tools": ("ls", "read_file", "glob", "grep"), "paths": ["/workspace/main"]},
    {"builtin_tools": ("write_file", "edit_file"), "paths": ["/workspace/main/output"]},
],
```

### Trusted local shell

```python
"permissions": [
    {"builtin_tools": ("write_todos", "execute", "task")},
    {"builtin_tools": ("ls", "read_file", "glob", "grep"), "paths": ["/workspace/main"]},
    {"builtin_tools": ("write_file", "edit_file"), "paths": ["/workspace/main/output"]},
],
```

Only use this profile when the deployment boundary already trusts the operator.

## Checklist Before Enabling Host Access

- Keep `ADMIN_AUTH_ENABLED=true` unless the deployment is trusted local only.
- Use long random admin passwords and token secrets.
- Omit `execute` unless command execution is required.
- Omit `write_file` and `edit_file` unless writes are required.
- Add explicit file-tool paths for the smallest useful paths.
- Prefer `state` for general chat.
