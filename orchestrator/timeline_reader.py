"""TimelineReader — tail a growing JSONL timeline file, yielding event batches.

Replaces the load-all-then-loop approach with streaming reads. The OME
appends new windows to the timeline file; this reader tails the file
(like `tail -f`) and groups events into timestamp-based batches.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class TimelineReader:
    """Tail a growing JSONL timeline file, yielding event batches."""

    def __init__(self, path: Path, epsilon_s: float = 0.1) -> None:
        self._path = path
        self._epsilon_s = epsilon_s
        self._file = open(path, "r")
        self._pending: list[dict] = []

    def next_batch(self, timeout_s: float = 5.0) -> list[dict] | None:
        """Read next timestamp-grouped batch. Returns None on timeout (no new data)."""
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            line = self._file.readline()
            if not line:
                # No new data yet — if we have pending events, flush them
                if self._pending:
                    return self._flush_batch()
                time.sleep(0.05)
                continue

            line = line.strip()
            if not line:
                continue

            record = json.loads(line)

            if (
                self._pending
                and abs(record["timestamp_s"] - self._pending[0]["timestamp_s"])
                >= self._epsilon_s
            ):
                # New timestamp group — flush current batch, keep this record for next
                batch = list(self._pending)
                self._pending = [record]
                return batch

            self._pending.append(record)

        # Timeout — flush whatever we have
        return self._flush_batch() if self._pending else None

    def _flush_batch(self) -> list[dict]:
        batch = list(self._pending)
        self._pending.clear()
        return batch

    def close(self) -> None:
        self._file.close()
