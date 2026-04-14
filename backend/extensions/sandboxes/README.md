# Sample Sandbox Configuration

Use `DEEPAGENTS_SANDBOX_KIND` and related `DEEPAGENTS_SANDBOX_*` settings to
target official DeepAgents backends first.

Upload-path guidance:

- User uploads are persisted before the agent run starts.
- Each upload is stored at `UPLOAD_STORAGE_DIR/<session_id>/<uuid>-<filename>`.
- `storage_key` is the path relative to `UPLOAD_STORAGE_DIR`; `upload_path` is the concrete absolute path handed to the runtime.
- The default `UPLOAD_STORAGE_DIR` resolves to `backend/data/uploads`, and the default read-only sandbox permissions already include `backend/data`.
- If you configure `UPLOAD_STORAGE_DIR` outside `backend/data`, the backend adds that directory to the runtime read permissions so uploaded files remain readable from the sandbox.
- Prompt/runtime guidance should tell the agent to use the provided upload path directly instead of asking the user to supply one again.

Examples:

- `state`
- `filesystem`
- `local_shell`
- `custom` with `DEEPAGENTS_SANDBOX_BACKEND_SPEC=module_or_path:factory`
