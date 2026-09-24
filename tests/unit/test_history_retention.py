# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""All history recordings together stay within one size budget, oldest data first."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.db.queries import get_metadata, insert_snapshot
from nodalarc.db.retention import (
    RETAINED_FROM_KEY,
    HistoryBudgetError,
    enforce_history_budget,
    used_bytes,
)
from nodalarc.db.schema import TIME_ORDERED_COLUMNS, create_tables

SESSION = "run-retention"
START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _recording(path, *, snapshots: int, size: int = 10_000, step_s: int = 10) -> None:
    conn = sqlite3.connect(path)
    try:
        create_tables(conn)
        for index in range(snapshots):
            at = (START + timedelta(seconds=step_s * index)).isoformat()
            insert_snapshot(
                conn, session_id=SESSION, sim_time=at, wall_time=at, snapshot_json="x" * size
            )
    finally:
        conn.close()


def test_every_table_that_grows_is_in_the_budget(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    try:
        create_tables(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert {table for table, _column in TIME_ORDERED_COLUMNS} == tables - {"session_metadata"}


def test_earlier_recordings_go_first_oldest_first(tmp_path):
    active = tmp_path / "run-active.db"
    _recording(active, snapshots=10)
    others = []
    for age, name in enumerate(("run-newer.db", "run-middle.db", "run-oldest.db")):
        path = tmp_path / name
        _recording(path, snapshots=10)
        mtime = (START - timedelta(hours=age)).timestamp()
        os.utime(path, (mtime, mtime))
        others.append(path)
    recording_bytes = others[0].stat().st_size

    conn = sqlite3.connect(active)
    try:
        active_bytes = used_bytes(conn)
        # Room for the active recording and one earlier one, within the trim target.
        budget = int((active_bytes + recording_bytes) / 0.9) + 1
        enforce_history_budget(conn, history_path=active, session_id=SESSION, max_bytes=budget)
        snapshots_kept = conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()

    assert sorted(path.name for path in tmp_path.glob("*.db")) == ["run-active.db", "run-newer.db"]
    assert snapshots_kept == 10


def test_the_recording_being_written_drops_its_oldest_rows_and_says_where_it_starts(tmp_path):
    active = tmp_path / "run-active.db"
    _recording(active, snapshots=100)
    conn = sqlite3.connect(active)
    try:
        # Link rows use isoformat's "+00:00"; snapshots from the state model use "Z".
        # The cutoff treats both as the same instants.
        for index in range(100):
            at = START + timedelta(seconds=10 * index)
            conn.execute(
                "INSERT INTO link_events (session_id, sim_time, wall_time, event_type, node_a,"
                " node_b) VALUES (?, ?, ?, 'LinkUp', 'sat-a', 'sat-b')",
                (SESSION, at.isoformat(), at.isoformat()),
            )
            conn.execute(
                "UPDATE snapshots SET wall_time = ? WHERE sim_time = ?",
                (at.isoformat().replace("+00:00", "Z"), at.isoformat()),
            )
        conn.commit()
        budget = used_bytes(conn) // 2

        enforce_history_budget(conn, history_path=active, session_id=SESSION, max_bytes=budget)

        assert used_bytes(conn) <= budget
        retained_from = datetime.fromisoformat(
            get_metadata(conn, session_id=SESSION, key=RETAINED_FROM_KEY)
        )
        oldest_link = datetime.fromisoformat(
            conn.execute("SELECT min(wall_time) FROM link_events").fetchone()[0]
        )
        oldest_snapshot = datetime.fromisoformat(
            conn.execute("SELECT min(wall_time) FROM snapshots").fetchone()[0]
        )
        newest_snapshot = conn.execute("SELECT max(sim_time) FROM snapshots").fetchone()[0]
    finally:
        conn.close()

    assert START < retained_from
    assert retained_from <= oldest_link
    assert retained_from <= oldest_snapshot
    assert oldest_link - oldest_snapshot < timedelta(seconds=10)
    assert newest_snapshot == (START + timedelta(seconds=990)).isoformat()


def test_a_recording_within_the_budget_keeps_everything(tmp_path):
    active = tmp_path / "run-active.db"
    _recording(active, snapshots=10)
    conn = sqlite3.connect(active)
    try:
        enforce_history_budget(
            conn, history_path=active, session_id=SESSION, max_bytes=used_bytes(conn) + 1
        )
        kept = conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]
        retained_from = get_metadata(conn, session_id=SESSION, key=RETAINED_FROM_KEY)
    finally:
        conn.close()

    assert (kept, retained_from) == (10, None)


def test_a_recording_made_at_one_instant_cannot_be_trimmed_to_fit(tmp_path):
    active = tmp_path / "run-active.db"
    _recording(active, snapshots=20, step_s=0)
    conn = sqlite3.connect(active)
    try:
        with pytest.raises(HistoryBudgetError, match="recorded at one instant"):
            enforce_history_budget(conn, history_path=active, session_id=SESSION, max_bytes=4096)
    finally:
        conn.close()
