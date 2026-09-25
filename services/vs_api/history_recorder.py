# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""One session run's history recording.

Handlers hand each write to ``submit`` and return at once: a writer thread
applies the writes in order on one connection and keeps all recordings within
the history size budget. A write that fails, or a write that finds the queue
already at its bound, stops the recording. The failure is logged as an error
and kept in ``error``, which every history read reports in place of data.
Nothing is retried and no write is dropped without stopping the recording.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from nodalarc.db.retention import enforce_history_budget

log = logging.getLogger(__name__)

# The write-ahead log is cut back to this size after each checkpoint; the one
# connection stays open for the whole recording, so the log is never removed.
# The rows of all recordings get the history budget less this.
_WAL_SIZE_LIMIT_BYTES = 16 * 1024 * 1024

HistoryWrite = Callable[[sqlite3.Connection], object]


class HistoryUnavailableError(Exception):
    """A history read refused: the session is not recorded, or its recording stopped."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class HistoryRecorder:
    """The writer of one session run's history file."""

    def __init__(
        self, path: Path, *, session_id: str, max_bytes: int, max_pending_writes: int
    ) -> None:
        if max_bytes <= _WAL_SIZE_LIMIT_BYTES:
            raise ValueError(
                f"history budget of {max_bytes} bytes leaves no room beside the "
                f"{_WAL_SIZE_LIMIT_BYTES}-byte write-ahead log"
            )
        self.path = path
        self._session_id = session_id
        self._rows_budget = max_bytes - _WAL_SIZE_LIMIT_BYTES
        self._queue: queue.Queue[tuple[str, HistoryWrite] | None] = queue.Queue(
            maxsize=max_pending_writes
        )
        self._state_lock = threading.Lock()
        self._error: str | None = None
        self._closed = False
        self._thread: threading.Thread | None = None

    @property
    def error(self) -> str | None:
        """Why the recording stopped, or None while it records."""
        with self._state_lock:
            return self._error

    def submit(self, what: str, write: HistoryWrite) -> None:
        """Queue one write. The caller never waits on the disk."""
        with self._state_lock:
            if self._error is not None or self._closed:
                return
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name=f"history-{self._session_id}", daemon=True
                )
                self._thread.start()
        try:
            self._queue.put_nowait((what, write))
        except queue.Full:
            self.stop(
                f"failed to record {what}: {self._queue.maxsize} writes were already waiting",
                None,
            )

    def stop(self, failure: str, exc: BaseException | None) -> None:
        """Stop the recording after a failure; later writes are not made."""
        with self._state_lock:
            if self._error is not None:
                return
            self._error = failure
        log.error(
            "History recording stopped for session %s: %s in %s%s",
            self._session_id,
            failure,
            self.path,
            f": {exc}" if exc is not None else "",
            exc_info=exc,
        )

    def flush(self) -> None:
        """Wait until every queued write has been applied or discarded."""
        self._queue.join()

    def close(self) -> None:
        """Apply the queued writes, then close the file and end the writer."""
        with self._state_lock:
            self._closed = True
            thread = self._thread
        if thread is None:
            return
        self._queue.put(None)
        thread.join()

    def _run(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is None:
                        return
                    if self.error is not None:
                        continue
                    what, write = item
                    try:
                        if conn is None:
                            conn = self._connect()
                        write(conn)
                        enforce_history_budget(
                            conn,
                            history_path=self.path,
                            session_id=self._session_id,
                            max_bytes=self._rows_budget,
                        )
                    except Exception as exc:
                        self.stop(f"failed to record {what}", exc)
                finally:
                    self._queue.task_done()
        finally:
            if conn is not None:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        # WAL with NORMAL syncs at checkpoints rather than on every commit. The
        # file stays consistent; a crash can lose the latest commits, as it
        # loses the writes still queued.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA journal_size_limit={_WAL_SIZE_LIMIT_BYTES}")
        return conn
