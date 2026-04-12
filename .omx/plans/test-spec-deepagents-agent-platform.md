# Test Spec: DeepAgents Agent Platform Scaffold

## Scope

Verification plan for the FastAPI + Vue 3 + JavaScript DeepAgents-based scaffold defined in `prd-deepagents-agent-platform.md`.

## Acceptance Criteria

1. The system boots with placeholder configuration and clear extension points for model API, tools, skills, middleware, sandbox provider, MySQL, and storage.
2. A user can create a session, view session history, and reopen a historical session.
3. A user can submit a prompt and receive streaming assistant output in the main chat area.
4. The UI renders markdown and mermaid output correctly.
5. The UI displays run progress, step completion, and failure states.
6. File upload works end to end with stored metadata and local-file persistence.
7. Session and message data are stored in MySQL.
8. The scaffold demonstrates reuse of DeepAgents runtime primitives rather than custom reimplementation.
9. Single-admin login gates the UI for v1.
10. The event bridge stores only UI-facing projections and does not become a second agent runtime.
11. The backend publishes a versioned SSE event contract that the Vue frontend validates at runtime.
12. Canonical transcript persistence stores only finalized messages, while transient stream chunks remain non-canonical.

## Unit Tests

### Backend

- config loading and validation
- auth token/session helpers
- session repository
- message repository
- agent run step normalizer
- DeepAgents event -> SSE event contract mapper
- SSE event schema versioning and runtime validation helpers
- storage provider abstraction
- sandbox adapter abstraction
- config-driven tool/skill/middleware loader wrapping official DeepAgents extension points

### Frontend

- session store
- SSE event reducer
- message renderer state transformations
- progress timeline state mapping
- upload form state

## Integration Tests

- create session -> persist -> list history
- submit prompt -> create run -> stream events -> persist final assistant message
- DeepAgents runtime event -> normalized SSE event -> frontend reducer state mapping
- stream reconnect -> dedupe by event ID -> no duplicate final transcript row
- upload file -> persist metadata -> bind file to run/session
- login -> access protected views
- tool/skill registration from configured extension directories
- sandbox adapter selection from configuration
- unauthenticated SSE client is rejected
- normalized event envelope includes the documented required fields for run, step, tool, skill, sandbox, and message events

## End-to-End Tests

- admin logs in, creates a new session, sends a prompt, watches streamed output, refreshes, and reopens history
- markdown and mermaid render in the transcript
- a long-running run shows incremental progress states in the UI
- upload a file before a prompt and confirm the attachment appears in the session

## Observability / Verification Signals

- structured backend logs for session ID, run ID, and step ID
- SSE connection lifecycle logs
- SSE event schema version in stream headers or initial handshake event
- DB migration status check
- upload storage path validation
- per-run status metrics: started, completed, failed

## Manual Verification

1. Boot backend, frontend, MySQL, and local file storage in dev mode.
2. Log in as the configured admin user.
3. Create two sessions and verify both appear in the sidebar.
4. Send a prompt and confirm streamed content arrives incrementally.
5. Confirm progress steps update while the run executes.
6. Reload the page and confirm transcript/history persists.
7. Upload a file and confirm metadata is stored and file path is valid.
8. Swap one extension adapter configuration and verify the scaffold still boots cleanly.

## Risks / Gaps to Watch

- DeepAgents event model may evolve, so the normalization layer needs contract tests.
- Mermaid rendering may require sanitization and fallback handling.
- SSE reconnect logic must avoid duplicating final transcript messages.
- Product/session read models must not become a second source of truth for execution semantics.
- JS frontend consumption requires stricter runtime validation because compile-time typing is intentionally absent.
- Upload validation needs explicit file-type allowlist, size limits, and path traversal protections.

## Team Verification Path

- `test-engineer` prepares unit/integration/e2e coverage plan from this file
- `verifier` validates acceptance criteria against running scaffold
- `security-reviewer` checks auth, upload handling, and sandbox boundaries
