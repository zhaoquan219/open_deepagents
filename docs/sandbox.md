# Sandbox Configuration

Use `DEEPAGENTS_SANDBOX_KIND` and the related `DEEPAGENTS_SANDBOX_*`
settings to select the DeepAgents backend that matches your deployment
boundary.

## Backend Kinds

| Kind | What it means | Typical use |
| --- | --- | --- |
| `state` | DeepAgents keeps a virtual in-memory file state. | Safest default for chat and tool-only runs. |
| `filesystem` | DeepAgents file tools operate under `DEEPAGENTS_SANDBOX_ROOT_DIR`. | Read/write a controlled backend directory. |
| `local_shell` | File tools plus `execute` can run on the host through DeepAgents. | Trusted local/operator environments only. |
| `custom` | A backend instance or factory loaded from `DEEPAGENTS_SANDBOX_BACKEND_SPEC`. | Bring your own isolation backend. |

`local_shell` is not process isolation. If you expose it, pair it with careful
permissions, tool filtering, and trusted users.

## Path Model

Upload records may carry three path-like fields:

- `storage_key`: relative to `UPLOAD_STORAGE_DIR`, for example
  `session-id/uuid-notes.txt`.
- `upload_path`: absolute host path, for example
  `/repo/backend/data/uploads/session-id/uuid-notes.txt`.
- `sandbox_path`: path to use from inside the configured sandbox root when the
  upload file is under that root.

When `DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true`, `sandbox_path` is formatted as a
virtual path with a leading slash, for example `/uploads/session-id/uuid-notes.txt`.
When virtual mode is false, DeepAgents receives normal backend path semantics.
For the default `state` backend, uploads are copied into the run's virtual file
state and `sandbox_path` is always a leading-slash virtual path under
`/uploads/`.

## Upload Visibility

- User uploads are persisted before the agent run starts.
- Each upload is stored at `UPLOAD_STORAGE_DIR/<session_id>/<uuid>-<filename>`.
- The default `UPLOAD_STORAGE_DIR=./data/uploads` resolves to
  `backend/data/uploads`, and default read-only runtime permissions include
  `backend/data`.
- If `UPLOAD_STORAGE_DIR` points outside `backend/data`, the backend
  automatically adds that upload directory to read permissions.
- The run-input hook receives the resolved attachment metadata, so you can change
  how paths are described to the model without editing `app/services/runs.py`.

## Examples

Safe default:

```dotenv
DEEPAGENTS_SANDBOX_KIND=state
```

Filesystem backend rooted under backend data:

```dotenv
DEEPAGENTS_SANDBOX_KIND=filesystem
DEEPAGENTS_SANDBOX_VIRTUAL_MODE=true
```

If `DEEPAGENTS_SANDBOX_ROOT_DIR` is omitted for `filesystem` or `local_shell`,
the backend defaults it to `backend/data`.

Trusted local shell, usually with write/execute tools filtered unless needed:

```dotenv
DEEPAGENTS_SANDBOX_KIND=local_shell
DEEPAGENTS_SANDBOX_ROOT_DIR=./data
DEEPAGENTS_DISABLED_BUILTIN_TOOLS=execute,write_file,edit_file
```

Custom backend:

```dotenv
DEEPAGENTS_SANDBOX_KIND=custom
DEEPAGENTS_SANDBOX_BACKEND_SPEC=extensions.custom_sandbox:build_backend
```
