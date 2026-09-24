# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The size budget all session history recordings share.

VS-API writes one history file per recorded run, all in one directory. Together
they may occupy at most the platform's ``vs_api_history_max_bytes``. Past it the
oldest recorded data goes first: earlier runs' files, oldest first, then the
oldest rows of the recording being written. A trim goes down to ``TRIM_TO`` of
the budget, so trims come in batches instead of on every write.

A recording that lost rows says so: its ``retained_from`` metadata holds the
time its oldest kept data was recorded at.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nodalarc.db.queries import set_metadata
from nodalarc.db.schema import TIME_ORDERED_COLUMNS

log = logging.getLogger(__name__)

TRIM_TO = 0.9
RETAINED_FROM_KEY = "retained_from"

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_JULIAN_DAY_OF_UNIX_EPOCH = 2440587.5


class HistoryBudgetError(RuntimeError):
    """A recording cannot fit the budget: everything it holds was recorded at one instant."""


def used_bytes(conn: sqlite3.Connection) -> int:
    """Bytes the rows of an open history file occupy; free pages are reused by later writes."""
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return (pages - free) * page_size


def enforce_history_budget(
    conn: sqlite3.Connection, *, history_path: Path, session_id: str, max_bytes: int
) -> None:
    """Keep every recording in ``history_path``'s directory within ``max_bytes`` together.

    ``conn`` is open on ``history_path``, the recording being written.
    """
    others = sorted(
        (path for path in history_path.parent.glob("*.db") if path != history_path),
        key=lambda path: path.stat().st_mtime,
    )
    other_bytes = {path: _file_bytes(path) for path in others}
    total = used_bytes(conn) + sum(other_bytes.values())
    if total <= max_bytes:
        return
    target = int(max_bytes * TRIM_TO)
    for path in others:
        if total <= target:
            return
        for file in (path, *_journal_files(path)):
            file.unlink(missing_ok=True)
        total -= other_bytes[path]
        log.warning(
            "History budget of %d bytes reached: removed the recording %s (%d bytes)",
            max_bytes,
            path.name,
            other_bytes[path],
        )
    if total > target:
        _drop_oldest_rows(conn, session_id=session_id, keep_bytes=target)


def _drop_oldest_rows(conn: sqlite3.Connection, *, session_id: str, keep_bytes: int) -> None:
    used = used_bytes(conn)
    while used > keep_bytes:
        oldest, newest = _recorded_span(conn)
        if oldest is None or newest is None or newest <= oldest:
            raise HistoryBudgetError(
                f"history of {session_id} holds {used} bytes recorded at one instant; "
                f"the budget keeps {keep_bytes}"
            )
        # Data arrives at a roughly steady rate, so dropping this share of the
        # recorded span frees about the excess; the loop measures and repeats.
        cutoff = oldest + (newest - oldest) * (used - keep_bytes) / used
        for table, column in TIME_ORDERED_COLUMNS:
            conn.execute(f"DELETE FROM {table} WHERE julianday({column}) < ?", (cutoff,))
        retained_from = _datetime_of_julian_day(cutoff).isoformat()
        set_metadata(conn, session_id=session_id, key=RETAINED_FROM_KEY, value=retained_from)
        remaining = used_bytes(conn)
        log.warning(
            "History budget reached: dropped the rows of %s recorded before %s (%d bytes)",
            session_id,
            retained_from,
            used - remaining,
        )
        used = remaining


def _recorded_span(conn: sqlite3.Connection) -> tuple[float | None, float | None]:
    """Julian days of the oldest and newest row in the time-ordered tables."""
    oldest: float | None = None
    newest: float | None = None
    for table, column in TIME_ORDERED_COLUMNS:
        low, high = conn.execute(
            f"SELECT min(julianday({column})), max(julianday({column})) FROM {table}"
        ).fetchone()
        if low is not None:
            oldest = low if oldest is None else min(oldest, low)
            newest = high if newest is None else max(newest, high)
    return oldest, newest


def _datetime_of_julian_day(julian_day: float) -> datetime:
    return _UNIX_EPOCH + timedelta(days=julian_day - _JULIAN_DAY_OF_UNIX_EPOCH)


def _journal_files(path: Path) -> tuple[Path, Path]:
    return path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")


def _file_bytes(path: Path) -> int:
    """A closed recording's size on disk, with any journal files beside it."""
    return sum(file.stat().st_size for file in (path, *_journal_files(path)) if file.exists())
