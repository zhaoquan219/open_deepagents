# open_deepagents Frontend

The frontend is a Vue 3 workspace for chatting with the configured DeepAgents
runtime. It handles login, session navigation, uploads, the message composer,
streamed responses, runtime progress, Markdown, Mermaid diagrams, and localized
user-facing copy.

## Directory Map

```text
frontend/
├── src/
│   ├── api/                 Backend API and SSE client
│   ├── components/          Workspace, sidebar, transcript, timeline, Mermaid
│   ├── lib/                 Copy, markdown, clipboard, scroll, SSE helpers
│   ├── store/               Session and run state
│   ├── App.vue              Main application shell
│   └── main.js              Vue bootstrap
├── tests/unit/              Vitest unit tests
├── package.json             Scripts and dependencies
└── vite.config.js           Vite configuration
```

## Local Development

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server defaults to `http://127.0.0.1:5173`.

The backend API defaults to `http://127.0.0.1:8000/api`. Configure CORS in
`backend/.env` with `CORS_ALLOWED_ORIGINS`.

## User Experience

- The sidebar lists sessions and lets users create, refresh, select, and delete
  sessions.
- The workspace shows the current transcript, upload controls, model selection,
  and the composer.
- Uploaded files appear as pending attachments before the run starts.
- Streaming assistant text appears in the transcript as it arrives.
- The runtime timeline shows connection, status, step, tool, skill, sandbox, and
  subagent events.
- Markdown is sanitized before insertion.
- Mermaid fenced code blocks render as diagrams.
- Mermaid image copy writes PNG data through the browser Clipboard API when
  supported.

## Scrolling Rules

The message thread follows the newest content while the user is already at the
bottom. If the user scrolls upward, streaming output does not pull the view back
down. Sending a new user message is treated as an explicit intent to return to
the newest content, so the thread jumps to the bottom.

The runtime timeline keeps only the most recent entries needed for the live
panel and coalesces repeated deltas, keeping long tool-heavy runs responsive.

## Backend Contract

The frontend expects these core endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/admin/login` | Login and receive a bearer token. |
| `GET /api/admin/me` | Check the current admin session. |
| `GET /api/runtime/options` | Load model options. |
| `GET /api/sessions` | List sessions for the current user. |
| `POST /api/sessions` | Create a session. |
| `GET /api/sessions/:sessionId/messages` | Load transcript messages. |
| `POST /api/sessions/:sessionId/uploads` | Upload a file for a session. |
| `DELETE /api/uploads/:uploadId` | Delete a pending upload. |
| `POST /api/runs` | Start an agent run. |
| `GET /api/runs/:runId/stream` | Stream run events with SSE. |
| `POST /api/runs/:runId/cancel` | Cancel an active run. |

## SSE Events

Incoming SSE payloads are normalized in `src/lib/sseContract.js`. The UI works
with these event families:

- `connection`
- `status`
- `message.delta`
- `message.final`
- `step`
- `tool`
- `skill`
- `subagent`
- `sandbox`
- `error`

Each event should include a stable event ID, run ID, session ID, timestamp,
status, label/detail text, and optional `data`. The run store deduplicates events
by event ID per run.

## Copy and Localization

All product copy lives in `src/lib/copy.js`. Edit copy there instead of
hard-coding strings in components. The current UI supports Chinese and English
copy trees.

Clipboard helpers live in `src/lib/clipboard.js`:

- text copy uses the Clipboard API with a textarea fallback;
- PNG copy uses the Clipboard API when available;
- image fallback is limited to browsers where it can provide a useful paste
  target.

## Commands

```bash
npm run dev
npm run lint
npm run test
npm run typecheck
npm run build
npm run check
```

`npm run check` runs lint, tests, typecheck, and production build.
