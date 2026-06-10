from __future__ import annotations

from threading import Lock


class RunControlRegistry:
    """In-memory cancellation signals for active runs.

    Cancellation is a control-plane concern, not a per-token data-plane one. The
    cancel endpoint and session archival flip an in-memory flag here, and the
    streaming loop checks it once per event. This keeps the hot streaming path off
    the product database entirely (previously every streamed token issued a status
    ``SELECT`` to learn whether the run had been cancelled, which churned the
    connection pool and added a remote round trip per token).

    The set is bounded by the number of concurrently active/cancelled runs: the
    streaming loop discards its run id when the stream ends.
    """

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._lock = Lock()

    def request_cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.discard(run_id)
