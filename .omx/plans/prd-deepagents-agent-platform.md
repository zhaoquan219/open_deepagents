# PRD: DeepAgents Agent Platform Scaffold

## Status

Consensus planning artifact. No implementation in this document.

## Goal

Build a front-end/back-end separated website scaffold for end users, with DeepAgents as the core agent runtime, FastAPI as the backend, and Vue 3 + JavaScript as the frontend. The scaffold must support:

- multi-turn chat
- streaming output
- markdown + mermaid rendering
- file upload
- left sidebar with new session + history
- MySQL-backed session/message persistence
- step/progress visibility
- extension points for custom tools, skills, middleware, sandboxes, and model providers

The scaffold must be usable after the operator fills in model API configuration, custom tool/skill implementations, middleware hooks, and MySQL credentials.

## Non-goals for the first scaffold

- full multi-tenant RBAC
- billing
- enterprise audit/compliance flows
- production-ready distributed job orchestration
- full object storage implementation on day one
- custom planner / executor / runtime abstractions that duplicate official DeepAgents behavior

## RALPLAN-DR Summary

### Principles

1. Reuse official DeepAgents primitives whenever they already solve the problem.
2. Keep the website layer thin: API, persistence, UI shell, and integration glue only.
3. Prefer reversible abstractions: adapter interfaces for sandbox, storage, auth, and model config.
4. Optimize for operable streaming UX: sessions, progress events, retries, and resumability.
5. Keep the first scaffold narrow enough to ship, but structured enough for later hardening.

### Decision Drivers

1. Avoid re-implementing agent/runtime logic already handled by DeepAgents.
2. Make the scaffold easy to customize by replacing adapters instead of editing core flow.
3. Keep the end-user UX clean while preserving detailed agent execution transparency.

### Viable Options

#### Option A: FastAPI + Vue 3 + SSE + thin DeepAgents integration layer

Pros:
- Best alignment with Python-native DeepAgents runtime.
- Lowest integration friction for tools, skills, middleware, sandboxes, and streaming.
- Clean front/back separation.

Cons:
- Requires a custom UI shell rather than using a packaged hosted UI out of the box.
- SSE is simpler than WebSocket but less flexible for bidirectional low-latency control.

#### Option B: FastAPI + Vue 3 + WebSocket-heavy runtime bridge

Pros:
- Richer real-time interaction model.
- Easier future support for collaborative or interrupt-heavy sessions.

Cons:
- Higher state-management complexity in both backend and frontend.
- More moving parts than required for the first scaffold.

#### Option C: Python agent core + separate Node/Nest BFF

Pros:
- Strong separation between frontend-facing API and Python runtime internals.
- Useful in larger multi-team organizations.

Cons:
- Adds a second backend stack without user value in the first scaffold.
- Violates the "thin integration layer" principle for this phase.

### Chosen Direction

Choose Option A.

Why:
- It best satisfies the selected constraints and the "do not reinvent DeepAgents" rule.
- It keeps the website layer thin and makes future replacement of storage, auth, and sandbox providers straightforward.

Why the alternatives are weaker:
- Option B is technically viable but adds complexity too early.
- Option C is organizationally useful only at a larger scale and is unjustified for the initial scaffold.

## ADR

### Decision

Adopt a monorepo scaffold with:

- `backend/`: FastAPI service, DeepAgents integration, MySQL persistence, SSE endpoints, upload endpoints, admin auth
- `frontend/`: Vue 3 + JavaScript SPA with sidebar, chat workspace, markdown/mermaid renderers, file upload, and progress timeline
- `shared/` or `packages/`: shared event schemas, API contracts, config types, and extension manifests
- `infra/`: local dev and deployment bootstrap

### Drivers

- Python-native DeepAgents runtime reuse
- minimal agent-layer reinvention
- clean future customization surface

### Alternatives considered

- WebSocket-first backend
- dual-backend architecture with Node BFF
- direct storage of everything only in DeepAgents runtime state

### Why chosen

- SSE covers streaming output and progress well enough for v1.
- FastAPI minimizes impedance mismatch with DeepAgents.
- MySQL-backed app persistence keeps product-level session history under explicit control.

### Consequences

- Need a well-defined event bridge from DeepAgents runtime events to frontend SSE.
- Need an app-layer session/message model in addition to agent runtime state.
- Need careful mapping between product sessions and DeepAgents execution threads/runs.
- Must prevent MySQL read models from becoming a second source of truth for agent execution state.
- Need a single contract source of truth for UI-facing SSE events because the frontend is Vue 3 + JavaScript rather than TypeScript.

### Follow-ups

- Add Redis-backed resumable streaming and job coordination later if long-running tasks require stronger durability.
- Add MinIO/S3 adapter after local file storage is stable.
- Add multi-user and RBAC in a later milestone.

## Reuse vs Implement

### Reuse directly from DeepAgents

- core agent runtime
- tool calling and tool registration patterns
- skills and subagents primitives
- sandbox/backend abstractions
- permissions / memory / middleware hooks where officially available
- frontend streaming concepts and event model patterns from official frontend docs

### Implement in the website scaffold

- FastAPI API surface for sessions, history, uploads, admin auth, and agent run launch
- MySQL persistence for product sessions, messages, run metadata, and attachments
- SSE bridge that exposes DeepAgents events to the frontend
- Vue application shell and UX components
- config-driven loaders and wrappers over official DeepAgents extension points for model API, custom tools, custom skills, custom middleware, sandbox provider, and storage provider

## Architectural Guardrails

1. DeepAgents owns agent execution state and execution semantics.
2. MySQL owns product/session UX state plus read-model projections of runtime activity for history and UI rendering.
3. The SSE bridge may normalize events for the UI, but it must not become a replacement runtime protocol.
4. Extensions must wrap official DeepAgents extension points instead of inventing parallel registries or planner/executor abstractions.
5. The scaffold may depend only on documented and version-pinned DeepAgents interfaces; undocumented internals are out of scope for v1.
6. Product persistence stores final transcript messages and UI-facing projections, not authoritative execution semantics.

## Compatibility Matrix

| Concern | V1 strategy | Required in V1 | Replaceable later |
| --- | --- | --- | --- |
| Model provider | Env-driven config wrapper over official DeepAgents model entry points | Yes | Yes |
| Tool loading | Configured module/directory loader that registers tools through official DeepAgents patterns | Yes | Yes |
| Skill loading | Configured module/directory loader that registers skills/subagents through official DeepAgents patterns | Yes | Yes |
| Middleware | Thin wrappers around official middleware hooks where available | Yes | Yes |
| Sandbox | Unified sandbox config that targets official DeepAgents sandbox/backend capabilities first | Yes | Yes |
| Storage | Local file storage provider with MinIO/S3-compatible interface reserved | Yes | Yes |
| Auth | Single-admin login wrapper around app shell and API access | Yes | Yes |
| MySQL persistence | Product/session read model and metadata store | Yes | No |

## Execution State and Event Contract Policy

### Runtime vs Persistence

- DeepAgents runtime state is authoritative during execution.
- MySQL stores product-facing history, attachment metadata, run metadata, and normalized read models for UI display.
- Streaming token chunks are transient transport data and are not persisted as canonical transcript rows.
- Only finalized assistant/user/system messages are stored as canonical transcript records.
- The system does not reconstruct DeepAgents runtime state from MySQL read models in v1.

### SSE Contract Source of Truth

- Backend owns the SSE event schema.
- The schema is published as a versioned JSON contract in `packages/` and consumed by the Vue app through runtime validation.
- Event IDs must be monotonic per run so reconnect logic can deduplicate safely.
- The normalization layer may flatten DeepAgents events for the UI, but every emitted event must map back to a documented bridge contract.

### Extension Loading Scope

- Tools, skills, middleware, and sandbox implementations are discovered from configured directories or modules at backend startup.
- v1 does not support hot-reloading or remote installation of extensions from the UI.
- The scaffold provides wrappers and manifests, not a second extension runtime.

## Proposed Architecture

### Backend layers

1. API Layer
- auth routes
- session routes
- message/run routes
- upload routes
- SSE stream endpoint

2. Application Layer
- session service
- agent run orchestration service
- upload service
- progress/event normalization service
- config service

3. DeepAgents Integration Layer
- agent factory
- tool registry adapter
- skill registry adapter
- middleware chain adapter
- sandbox adapter
- runtime event adapter

4. Persistence Layer
- SQLAlchemy models / repositories
- file storage provider
- migration layer

### Frontend layers

1. App shell
- sidebar
- session list
- new session action
- top status bar

2. Workspace
- chat transcript
- composer
- upload area
- render pipeline for markdown + mermaid

3. Execution visibility
- run status banner
- step/progress timeline
- tool/skill/sandbox event panes

4. Data/state
- API client
- SSE client
- session store
- run state store

## Module Boundaries

### Backend

- `backend/app/api`
- `backend/app/core`
- `backend/app/auth`
- `backend/app/sessions`
- `backend/app/chat`
- `backend/app/uploads`
- `backend/app/agents`
- `backend/app/extensions`
- `backend/app/storage`
- `backend/app/db`

### Frontend

- `frontend/src/layouts`
- `frontend/src/pages`
- `frontend/src/components/chat`
- `frontend/src/components/session`
- `frontend/src/components/progress`
- `frontend/src/components/renderers`
- `frontend/src/stores`
- `frontend/src/services`
- `frontend/src/composables`

## Data Model Outline

- `admin_users`
  single-admin login for v1
- `chat_sessions`
  product-level session metadata
- `chat_messages`
  user/assistant/system/tool-visible transcript records
- `agent_runs`
  one execution run per submitted turn
- `run_event_views`
  normalized UI-facing projections of runtime events for timeline and status rendering, not a second execution state machine
- `attachments`
  uploaded file metadata and storage path
- `session_runtime_links`
  mapping between app session IDs and DeepAgents runtime thread/run identifiers

## API Surface Outline

- `POST /api/auth/login`
- `POST /api/sessions`
- `GET /api/sessions`
- `GET /api/sessions/{id}`
- `POST /api/sessions/{id}/messages`
- `GET /api/sessions/{id}/stream`
- `POST /api/uploads`
- `GET /api/runs/{id}`

## Delivery Sequence

1. Define backend event contract, runtime-link policy, and extension-loading contract before wiring the UI.
2. Implement backend boot path, admin auth, session CRUD, and MySQL schema.
3. Integrate DeepAgents through documented entry points only, then expose SSE bridge events.
4. Build the Vue shell against the contract, including session list, chat transcript, progress timeline, and upload flow.
5. Add extension adapters for tools, skills, middleware, sandbox, and storage.
6. Harden reconnect, dedupe, sanitization, and upload/sandbox boundaries.

## Streaming/Event Contract

Use SSE for:

- assistant token/output deltas
- run started/completed/failed
- step started/completed/failed
- tool invocation events
- skill/subagent/sandbox progress events
- attachment processing events

Normalize DeepAgents runtime events into a frontend-safe event schema rather than exposing raw internal objects directly.

Required normalized event envelope:

- `event_version`
- `session_id`
- `run_id`
- `message_id` when applicable
- `step_id` when applicable
- `sequence`
- `event_type`
- `status`
- `ts`
- `payload`

Contract rules:

- SSE events are auth-protected and scoped to one session/run context.
- `sequence` is monotonic within a run and used for reconnect dedupe.
- Final assistant content is persisted once and replay-safe on reconnect.
- Mermaid-capable content is rendered from sanitized markdown, not raw HTML passthrough.
- Raw DeepAgents internal objects are never sent directly to the browser.

## Phased Implementation Plan

### Phase 0: Scaffold foundation

- create monorepo structure
- backend and frontend dev bootstrap
- env/config templates
- DB migration baseline
- extension manifest and adapter contracts

### Phase 1: Backend runtime bridge

- FastAPI app shell
- session/message models
- DeepAgents agent factory
- custom tool/skill/middleware/sandbox adapter interfaces
- SSE event bridge

### Phase 2: Frontend product shell

- sidebar + new session + history
- chat workspace
- markdown/mermaid renderers
- streaming transcript
- progress timeline

### Phase 3: Uploads + admin auth + persistence hardening

- single-admin login
- file upload flows
- local file storage provider
- session restoration/history loading

### Phase 4: polish and extension proof

- sample custom tool
- sample custom skill
- sample middleware hook
- sample sandbox provider config
- MinIO/S3 adapter placeholder

## Risks

- DeepAgents runtime event shape may need a normalization layer for stable frontend consumption.
- Session persistence at the product layer must not conflict with DeepAgents internal runtime state semantics.
- Sandbox capability differences across providers require an adapter boundary from day one.
- SSE reconnect, dedupe, and final-message replay rules can drift if the event envelope is not contract-tested.
- File uploads and Mermaid rendering both expand the attack surface and need explicit validation/sanitization policies.

## Available Agent Types for Later Execution

- `planner`: sequencing, milestone refinement
- `architect`: runtime boundaries, event contract, extension design
- `executor`: backend/frontend scaffold implementation
- `test-engineer`: test plan and coverage
- `verifier`: completion evidence
- `security-reviewer`: auth, upload, sandbox boundary review
- `designer`: Vue UX structure and interaction polish

## Staffing Guidance

### If executed with `ralph`

- One sequential owner implements in this order:
  1. backend foundation
  2. agent/runtime bridge
  3. frontend shell
  4. auth/uploads/storage
  5. verification
- Suggested reasoning levels:
  - architecture/event-contract decisions: high
  - backend CRUD and persistence: medium
  - frontend shell wiring: medium
  - test/verification pass: high

### If executed with `team`

- Lane 1: backend API + persistence
- Lane 2: DeepAgents integration + extension adapters
- Lane 3: frontend shell + chat UI + SSE client
- Lane 4: test/verification lane
- Suggested reasoning by lane:
  - Lane 1 backend API + persistence: medium
  - Lane 2 DeepAgents integration + adapters: high
  - Lane 3 frontend shell + SSE client: medium
  - Lane 4 test/verification: high

## Launch Hints for Later

- `$ralph implement the approved PRD in /Users/zhaoquan/AI_Coding/open_deepagents using the phased plan in .omx/plans/prd-deepagents-agent-platform.md`
- `$team implement the approved scaffold plan in parallel using backend, deepagents-integration, frontend, and verification lanes`
- `omx team --cwd /Users/zhaoquan/AI_Coding/open_deepagents "Implement the approved scaffold plan using the four lanes in .omx/plans/prd-deepagents-agent-platform.md"`
