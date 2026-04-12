Task statement

Plan a full-stack, front-end/back-end separated agent website scaffold based on DeepAgents as the core. The scaffold must support custom tools, custom skills, sandbox isolation, pluggable middleware, session management, historical conversations, streaming multi-turn chat, Markdown and Mermaid rendering, file upload, step/progress display, and MySQL-backed persistence.

Desired outcome

Produce a consensus implementation plan only, not code implementation yet. The plan should offer multiple technology choices, let the user choose actively, and converge through multiple rounds of interaction into a clear execution-ready scaffold plan.

Known facts / evidence

- The workspace currently contains a local Python environment scaffold for DeepAgents experimentation.
- The user explicitly asked not to implement directly.
- The user wants a reusable, highly customizable agent website scaffold that can be launched after filling in model APIs, custom tools, skills, middleware, and MySQL credentials.
- The user explicitly invoked ralplan and asked for multi-round interaction before a full implementation plan.
- The user selected FastAPI for the backend.
- The user selected Vue 3 + JavaScript for the frontend.
- The user explicitly wants the system to fully reuse DeepAgents built-in capabilities where available, especially for tools, skills, sandboxes, and middleware.

Constraints

- Planning only for now; no implementation.
- Must provide multiple technical options and tradeoffs instead of a single forced stack.
- Frontend should be simple and elegant.
- Backend must use DeepAgents as the core agent framework.
- Persistence should support MySQL for sessions and chat records.
- Architecture should leave extension points for tools, skills, middleware, sandboxing, and file handling.
- Avoid re-implementing capabilities already provided by DeepAgents when an official extension point exists.

Unknowns / open questions

- Preferred Vue UI/state stack.
- Preferred backend API style (REST + SSE, WebSocket, hybrid).
- Preferred async/task execution model.
- Preferred sandbox isolation strategy (Docker, process-level, remote sandbox).
- Deployment preference (single VPS, Docker Compose, k8s, serverless mix).
- Auth requirements for users/admins.
- File storage preference (local disk, S3/MinIO, OSS).
- Whether the first scaffold should include RBAC, observability, billing, multi-tenant support.

Likely codebase touchpoints

- New project scaffold directories for frontend and backend.
- Shared configuration/env templates.
- Backend modules for agent runtime, tool registry, middleware chain, session service, chat persistence, uploads, and streaming.
- Frontend modules for layout, sidebar, chat view, rendering pipeline, upload UI, and progress/timeline UI.
- Docker/devcontainer/deployment bootstrap.
