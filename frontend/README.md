# Frontend shell

Vue 3 + JavaScript frontend scaffold for the DeepAgents agent platform.

## Included in this lane

- session sidebar with create/select/refresh flows
- transcript workspace with composer and upload affordances
- markdown + mermaid rendering for transcript content
- SSE runtime event consumption with runtime envelope validation and event dedupe
- progress timeline for run, step, tool, skill, and sandbox events
- lightweight docs for the backend contract this UI expects

## Commands

```bash
npm install
npm run dev
npm run check
```

## Expected backend surface

The shell is intentionally thin and expects backend endpoints owned by the backend/runtime lanes:

- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/:sessionId/messages`
- `POST /api/uploads`
- `POST /api/runs`
- `GET /api/runs/:runId/stream`

## SSE envelope assumptions

The frontend accepts either snake_case or camelCase fields and normalizes them into:

```json
{
  "version": "deepagents-ui.v1",
  "event_id": "evt-001",
  "type": "message.delta",
  "session_id": "sess-123",
  "run_id": "run-123",
  "timestamp": "2026-04-12T14:00:00Z",
  "data": {
    "delta": "partial token stream"
  }
}
```

Supported event families: `status`, `message.delta`, `message.final`, `step`, `tool`, `skill`, `sandbox`, and `error`.

## Notes for integration

- Finalized transcript rows are derived from `message.final` events; token chunks stay transient in UI state.
- Event dedupe is keyed by `event_id` per run.
- Mermaid blocks are rendered from fenced code blocks using `mermaid` with `securityLevel: 'strict'`.
- HTML output is sanitized before insertion so backend-delivered markdown cannot inject scripts or inline event handlers.
