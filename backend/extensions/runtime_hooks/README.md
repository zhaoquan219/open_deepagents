# Runtime hook templates

Use this folder for project-specific runtime behavior that should not live in
`app/services/runs.py`.

Enable the bundled examples from `backend/.env`:

```dotenv
DEEPAGENTS_RUN_INPUT_HOOK_SPECS=extensions/runtime_hooks/__init__.py:RUN_INPUT_HOOKS
DEEPAGENTS_UPLOAD_HOOK_SPECS=extensions/runtime_hooks/__init__.py:UPLOAD_HOOKS
```

Hook entrypoints use the same `path/to/file.py:OBJECT_NAME` format as tools and
middleware. You can export one function or a list/tuple of functions.

Run input hooks receive a `RunInputHookContext` and return:

- a string replacement for the message content
- `{"content": "..."}` for the same replacement
- `None` to leave content unchanged

Upload hooks receive an `UploadHookContext` after the file is stored and return:

- a mapping to merge into the upload record `extra`
- `None` to leave `extra` unchanged

Keep hooks deterministic and fast. They run in the request/run path.
