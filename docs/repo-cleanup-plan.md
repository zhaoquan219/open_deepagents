# Repo Cleanup Plan

## Goal

Make the repository readable at a glance by aligning the file layout with the
actual runtime path and removing misleading scaffold leftovers.

## Scope

This cleanup is intentionally bounded to repository structure and import paths.
It must not change runtime behavior.

In scope:

- remove frontend pass-through wrapper files and converge on one canonical
  directory layout
- remove backend placeholder packages that are not imported anywhere
- delete obsolete root helper scripts and their config scaffolding
- update tests, verification rules, and docs to match the new layout

Out of scope:

- changing backend API behavior
- changing frontend UI behavior
- changing DeepAgents runtime logic
- introducing new dependencies

## Behavior Lock

The current structure is protected by these baseline checks before cleanup:

- `cd frontend && npm run test`
- `cd backend && uv run pytest`
- `uv run python -m unittest discover -s tests`

## Smells To Remove

1. Needless abstraction
   - frontend wrapper files that only re-export another module
2. Dead code
   - unused frontend composables and backend placeholder packages
3. Boundary noise
   - helper scripts mixed into the repository root
4. Misleading verification
   - scaffold audit rules that enforce empty placeholder directories instead of
     the real runtime structure

## Execution Order

1. Frontend cleanup
   - keep one canonical location for app shell, API client, components, and stores
   - delete pass-through wrappers
2. Backend cleanup
   - delete unused placeholder packages
   - move local storage implementation under `app/storage/`
   - align scaffold verification with the real backend layout
3. Root cleanup
   - remove obsolete helper scripts
   - remove root-only config scaffolding that exists only for those scripts
   - update README and references
4. Verification
   - run frontend tests
   - run backend tests
   - run repo-level tests
   - run frontend lint/typecheck/build

## Target Shape

```text
.
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── storage/
│   ├── deepagents_integration/
│   ├── extensions/
│   └── tests/
├── docs/
├── frontend/
│   └── src/
│       ├── api/
│       ├── components/
│       ├── lib/
│       └── store/
├── packages/
├── tests/
└── verification/
```
