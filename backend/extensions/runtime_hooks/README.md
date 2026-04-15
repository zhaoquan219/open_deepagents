# Runtime hook templates

Use this folder for project-specific runtime behavior that should not live in
`app/services/runs.py`.

Enable the bundled defaults from `backend/.env`:

```dotenv
DEEPAGENTS_RUN_INPUT_HOOK_SPECS=extensions.runtime_hooks:RUN_INPUT_HOOKS
DEEPAGENTS_UPLOAD_HOOK_SPECS=extensions.runtime_hooks:UPLOAD_HOOKS
```

Hook entrypoints use the same `path/to/file.py:OBJECT_NAME` format as tools and
middleware. You can export one function or a list/tuple of functions.

If either env var is blank, that hook lane is intentionally disabled. Uploads
will still be persisted and bound to messages, but blank `RUN_INPUT_HOOKS` means
no attachment text is injected into the message sent to DeepAgents.

Run input hooks receive a `RunInputHookContext` and return:

- a string replacement for the message content
- `{"content": "..."}` for the same replacement
- `None` to leave content unchanged

Upload hooks receive an `UploadHookContext` after the file is stored and return:

- a mapping to merge into the upload record `extra`
- `None` to leave `extra` unchanged

Keep hooks deterministic and fast. They run in the request/run path.
