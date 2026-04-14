# Repository Architecture

## Design Goal

A developer should be able to answer three questions immediately:

1. Where does the product run?
2. Where does shared contract logic live?
3. Which files are support tooling rather than runtime code?

This repository is organized around those answers.

## Top-Level Structure

```text
.
├── backend/        FastAPI runtime and DeepAgents backend integration
├── frontend/       Vue operator UI
├── packages/       Shared contracts and manifests
├── verification/   Repo-level validation helpers
├── tests/          Repo-level tests for contracts and scaffold rules
└── docs/           Human-facing architecture and maintenance notes
```

## Runtime Paths

### Backend

The backend runtime path is intentionally shallow:

```text
backend/
├── app/
│   ├── api/        Route registration, dependencies, HTTP surfaces
│   ├── core/       Settings, auth, database wiring
│   ├── db/         SQLAlchemy models and base classes
│   ├── schemas/    Request/response models
│   ├── services/   Run orchestration and app services
│   └── storage/    Storage implementations
├── deepagents_integration/
├── extensions/
└── tests/
```

Rules:

- `app/api` owns HTTP concerns only.
- `app/core` owns process wiring and shared infrastructure state.
- `app/services` owns runtime coordination, not HTTP parsing.
- `app/storage` owns file persistence implementations.
- `deepagents_integration` is external-runtime glue, not generic app code.

Removed from the canonical path:

- empty placeholder packages such as `app/agents`, `app/auth`, `app/chat`,
  `app/sessions`, `app/uploads`, and `app/extensions`

### Frontend

The frontend uses a flat, small-app layout:

```text
frontend/src/
├── App.vue         Root UI composition
├── api/            Backend HTTP client
├── components/     Reusable view components
├── lib/            Pure rendering and normalization helpers
├── store/          Client-side state containers
└── styles.css
```

Rules:

- `App.vue` composes the screen and owns page-level behavior.
- `api/` is the only place that should know fetch and endpoint details.
- `components/` contains presentational and interaction units.
- `lib/` contains pure helpers such as markdown rendering and SSE normalization.
- `store/` contains state transitions and view-model logic.
- `frontend/tests/unit/` contains Vitest coverage and stays separate from source files.

Removed from the canonical path:

- pass-through wrapper directories such as `components/chat`,
  `components/progress`, `components/renderers`, `components/session`,
  `layouts`, `pages`, `services`, `stores`, and `composables`

## Shared and Support Layers

### `packages/`

Contains artifacts shared across lanes:

- contract schemas
- extension manifest examples

### `verification/` and `tests/`

- `verification/` contains reusable audit and validation code
- `tests/` contains repo-level tests that exercise those helpers

## Practical Reading Order

For a new developer, the fastest way to understand the project is:

1. read `README.md`
2. read this file
3. inspect `backend/app/main.py`
4. inspect `frontend/src/App.vue`
5. inspect `backend/app/services/runs.py`
6. inspect `frontend/src/api/client.js`

That sequence follows the real request path instead of the historical scaffold
shape.
