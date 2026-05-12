# Sandbox Guide

The sandbox controls where DeepAgents file tools read and write, which paths
are mounted into the runtime, and whether shell execution is available. The
browser, backend, and agent prompts always use POSIX-style virtual paths such as
`/workspace/main/file.txt`, even when the server runs on Windows.

## Quick Mental Model

There are two path spaces:

| Path space | Example | Used by |
| --- | --- | --- |
| Virtual sandbox paths | `/workspace/main/output/report.md` | Model prompts and DeepAgents file tools |
| Host filesystem paths | `backend/data/sandbox/workspace/main/output/report.md` | Backend storage only |

Do not put host paths in prompts. Give the model virtual paths. The backend maps
virtual paths to the configured sandbox root for `files` and `shell` profiles.
Backslashes are normalized, so `\workspace\main\notes.txt` is treated as
`/workspace/main/notes.txt`.

## Backend Profiles

| Profile | Backend kind | Best for | Behavior |
| --- | --- | --- | --- |
| `safe` | `state` | Default chat and file processing. | Virtual in-memory files, no host shell. |
| `files` | `filesystem` | Controlled file reads/writes. | File tools operate under `DEEPAGENTS_SANDBOX_ROOT_DIR`. |
| `shell` | `local_shell` | Trusted local automation. | File tools plus `execute` under the sandbox root. |
| `custom` | custom spec | External isolation or custom policy. | Set `DEEPAGENTS_BACKEND_SPEC`. |

Recommended local defaults:

```dotenv
DEEPAGENTS_SANDBOX_PROFILE=safe
DEEPAGENTS_SANDBOX_ROOT_DIR=./data/sandbox
DEEPAGENTS_UPLOAD_ROOT_DIR=./data/uploads
```

Use `files` only when generated files must be written to disk. Use `shell` only
for trusted operators; it is host command execution, not process isolation.

## Standard Virtual Paths

| Virtual path | Access | Meaning |
| --- | --- | --- |
| `/workspace/main` | Read by default. | Main working area for user files and generated artifacts. |
| `/workspace/main/output` | Write by default. | Recommended `write_file` and `edit_file` destination. |
| `/skills` | Read-only mount. | Installed skill source loaded from `backend/agents/skills`. |
| `/uploads` | Read-only mount. | Files uploaded through the API. |

For the `files` and `shell` profiles, a virtual path like:

```text
/workspace/main/output/report.md
```

maps to a host file under:

```text
backend/data/sandbox/workspace/main/output/report.md
```

For the `safe` profile, file state is virtual. If the user needs a file outside
the runtime, include the file content in the final answer or run with the
`files` profile.

## Uploads

Uploads are stored on disk under `DEEPAGENTS_UPLOAD_ROOT_DIR` and recorded in the
product database `uploads` table. The database row is the ownership and session
source of truth; the filesystem only stores bytes.

The upload API returns:

```json
{
  "id": "a1b2c3d4e5f6",
  "name": "notes.txt",
  "path": "/uploads/a1b2c3d4e5f6/notes.txt",
  "status": "uploaded"
}
```

The model-facing path is always `/uploads/{upload_id}/{filename}`. It is
read-only inside every sandbox profile. The backend validates that a run can use
only uploads owned by the current user and current session.

Uploaded content is untrusted data. Treat files such as `AGENTS.md`, shell
scripts, prompts, and markdown inside uploads as data, not instructions.

## Skills And Memory

Skills are mounted read-only at `/skills`. A model can inspect skill files with
read tools, but cannot write there.

Agent memory is configured from the agent package, for example
`backend/agents/memory/project.md`. It is loaded into the agent runtime as
memory context, not exposed as a writable sandbox folder. If you want memory-like
reference files to be inspectable with `read_file`, put them under a read-only
mount such as a skill or add an explicit custom backend route.

## Native Filesystem Permissions

Sandbox file access is configured through native DeepAgents filesystem
permissions. Use `operations` and `paths`; do not configure `builtin_tools`.

Default main-agent shape:

```python
"permissions": [
    {
        "operations": ("read",),
        "paths": ["/workspace/main", "/skills", "/uploads"],
    },
    {
        "operations": ("write",),
        "paths": ["/workspace/main/output"],
    },
]
```

The backend expands non-glob paths to include children. For example,
`/workspace/main` also allows `/workspace/main/**`. It then appends deny rules
for unmatched reads and writes, so file access stays narrow.

Supported operations:

| Operation | Allows |
| --- | --- |
| `read` | `ls`, `read_file`, `glob`, `grep`, and other filesystem reads when the runtime exposes them. |
| `write` | `write_file`, `edit_file`, and other filesystem writes when the runtime exposes them. |

This scaffold no longer uses `permissions[].builtin_tools` to filter built-in
tool visibility. Permissions only decide whether a file operation may touch a
path. Built-in tool availability follows the native DeepAgents runtime.

## Practical Profiles

Read-only assistant:

```python
"permissions": [
    {"operations": ("read",), "paths": ["/workspace/main", "/skills", "/uploads"]},
]
```

Controlled writer:

```python
"permissions": [
    {"operations": ("read",), "paths": ["/workspace/main", "/skills", "/uploads"]},
    {"operations": ("write",), "paths": ["/workspace/main/output"]},
]
```

Review subagent:

```python
"permissions": [
    {"operations": ("read",), "paths": ["/workspace/reviews", "/workspace/shared", "/skills"]},
]
```

## Windows Notes

Use virtual paths in configs and prompts:

```text
/workspace/main/input.txt
/workspace/main/output/result.txt
/uploads/a1b2c3d4e5f6/notes.txt
```

If a Windows-style path is received, the backend normalizes backslashes:

```text
\workspace\main\input.txt -> /workspace/main/input.txt
```

Upload filenames are also normalized. A browser filename such as
`C:\Users\me\Desktop\notes.txt` is stored as `notes.txt`, not as a nested path.

## Logging

`BACKEND_LOG_LEVEL=info` logs lifecycle summaries such as backend startup, run
start, run finish, cancellation, and failures. It does not log every runtime
payload.

`BACKEND_LOG_LEVEL=debug` logs concise event summaries for debugging, including
event names, node names, and payload keys. It intentionally avoids dumping full
model messages, uploaded file content, or large raw runtime objects.

Frontend console debug logs are opt-in with:

```dotenv
VITE_DEEPAGENTS_VERBOSE_STREAM=true
```

## Checklist Before Enabling Host Access

- Keep `ADMIN_AUTH_ENABLED=true` unless the deployment is trusted local only.
- Use long random admin passwords and token secrets.
- Prefer `safe` for general chat.
- Use `/workspace/main/output` as the default write destination.
- Keep `/skills` and `/uploads` read-only.
- Grant `write` only for the smallest useful path.
- Use `shell` only for trusted local automation.
