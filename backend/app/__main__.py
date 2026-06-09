"""Console entry point: ``python -m app``.

This launcher exists so the asyncio event loop policy can be configured *before*
uvicorn binds its listening socket. On Windows the default ``ProactorEventLoop``
cannot drive async psycopg (used by LangGraph's Postgres checkpoint/store), while
the ``SelectorEventLoop`` can. Setting the policy here -- in the process that runs
``uvicorn.run`` and therefore binds the socket -- keeps the parent reload process,
the spawned worker, and the per-run worker threads consistent. Using the bare
``uvicorn app.main:app`` CLI cannot do this because that CLI binds the socket
before importing the app, leaving a Proactor-bound socket served by a Selector
loop (which silently hangs).

Everything here is a no-op on Linux/macOS, where the default loop already works.
"""

from __future__ import annotations

import asyncio
import os
import sys


def _configure_event_loop() -> None:
    if sys.platform != "win32":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    try:
        from app.settings import get_settings

        needs_postgres = get_settings().runtime_driver_mode() == "postgres"
    except Exception:
        needs_postgres = False
    if not needs_postgres:
        return
    if isinstance(asyncio.get_event_loop_policy(), selector_policy):
        return
    asyncio.set_event_loop_policy(selector_policy())


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    _configure_event_loop()

    import uvicorn

    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    reload_enabled = _env_flag("BACKEND_RELOAD", default=True)
    workers = max(1, int(os.getenv("BACKEND_WORKERS", "1")))

    # Each worker process gets its own runtime loop, checkpointer, and DB
    # connections, so graph runs scale horizontally across CPU cores. Reload and
    # multi-worker mode are mutually exclusive in uvicorn.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        workers=None if reload_enabled else workers,
    )


if __name__ == "__main__":
    main()
