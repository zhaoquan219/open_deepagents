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
| `custom` | External isolation service or custom policy. | Loads a backend instance or factory from an import spec. |

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
DEEPAGENTS_SANDBOX_ROOT_DIR=./data
DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true
```

Trusted local shell:

```dotenv
DEEPAGENTS_SANDBOX_KIND=local_shell
DEEPAGENTS_SANDBOX_ROOT_DIR=./data
DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true
```

Custom backend:

```dotenv
DEEPAGENTS_SANDBOX_KIND=custom
DEEPAGENTS_SANDBOX_BACKEND_SPEC=path/to/custom_sandbox.py:build_backend
```

Custom specs can point to an importable module or a Python file path.

## Virtual Paths

For `filesystem` and `local_shell`, the app uses virtual path semantics. Inside
DeepAgents file tools, `/` maps to `DEEPAGENTS_SANDBOX_ROOT_DIR`, not to the host
filesystem root.

Example:

```dotenv
DEEPAGENTS_SANDBOX_ROOT_DIR=./data
```

A tool path like `/uploads/session/file.txt` maps to:

```text
backend/data/uploads/session/file.txt
```

This path model keeps prompts and tools stable across macOS, Linux, and Windows.

## Upload Paths

Upload records include:

- `storage_key`: path relative to `UPLOAD_STORAGE_DIR`;
- `upload_path`: absolute host path;
- `sandbox_path`: path the model should use with file tools.

For `state`, uploads are copied into virtual files under `/uploads/...`.
For `filesystem` and `local_shell`, `sandbox_path` is emitted only when the file
is inside the configured sandbox root.

The default `UPLOAD_STORAGE_DIR=./data/uploads` keeps uploads under
`backend/data/uploads`, which is inside the default sandbox root.

## Generated Files

When the `state` backend finishes a run, the backend compares final virtual file
state with the initial uploaded files. New or changed files are exported to
`UPLOAD_STORAGE_DIR`, inserted as upload records, and attached to the final
assistant message.

Large or binary state payloads are summarized or redacted in runtime events so
logs and UI event storage remain responsive.

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
    {"operations": ["read"], "paths": ["/workspace/main", "/uploads"]},
],
```

### Controlled writer

```python
"builtin_tools": ("write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "task"),
"disabled_builtin_tools": ("execute",),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main", "/uploads"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
],
```

### Trusted local shell

```python
"builtin_tools": ("write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "task"),
"permissions": [
    {"operations": ["read"], "paths": ["/workspace/main", "/uploads"]},
    {"operations": ["write"], "paths": ["/workspace/main/output"]},
],
```

Only use this profile when the deployment boundary already trusts the operator.

## Checklist Before Enabling Host Access

- Keep `ADMIN_AUTH_ENABLED=true` unless the deployment is trusted local only.
- Use long random admin passwords and token secrets.
- Keep uploads inside the sandbox root when models need file-tool access.
- Hide `execute` unless command execution is required.
- Hide `write_file` and `edit_file` unless writes are required.
- Add explicit read/write permissions for the smallest useful paths.
- Prefer `state` for general chat and upload analysis.
