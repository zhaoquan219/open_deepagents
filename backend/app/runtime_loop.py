"""A single dedicated asyncio event loop for LangGraph runtime persistence.

LangGraph's async Postgres/SQLite checkpoint and store objects create internal
``asyncio.Lock`` instances that bind to the event loop they are first used on.
psycopg's async mode additionally requires a ``SelectorEventLoop`` on Windows.

Running the server's HTTP loop *and* the agent graph on different loops (the
previous design ran each run in its own throwaway loop) makes those locks raise
``<asyncio.locks.Lock ...> is bound to a different event loop`` as soon as the
run touches the checkpointer/store.

This module provides one long-lived loop, running on a dedicated thread, that
owns the runtime checkpointer/store and executes every graph run and readiness
probe. Because all of those awaits happen on the same loop, the locks stay
valid; because the loop is explicitly a ``SelectorEventLoop`` on Windows, async
psycopg works regardless of the server's main loop policy.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import TypeVar

_T = TypeVar("_T")


class RuntimeLoop:
    """Owns a background event loop for runtime persistence and graph execution."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._loop = self._new_loop()
        self._thread = threading.Thread(
            target=self._run,
            name="deepagents-runtime-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    @staticmethod
    def _new_loop() -> asyncio.AbstractEventLoop:
        if sys.platform == "win32":
            # psycopg async mode cannot run on Windows' ProactorEventLoop.
            return asyncio.SelectorEventLoop()
        return asyncio.new_event_loop()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            finally:
                self._loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def submit(self, coro: Coroutine[object, object, _T]) -> Future[_T]:
        """Schedule a coroutine on the runtime loop; returns a concurrent Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[object, object, _T]) -> _T:
        """Run a coroutine on the runtime loop and block until it completes.

        Must be called from a thread other than the runtime loop's own thread.
        """
        return self.submit(coro).result()

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
