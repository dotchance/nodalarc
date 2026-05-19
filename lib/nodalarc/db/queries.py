# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed insert/query functions for Nodal Arc SQLite database.

All functions accept and return Pydantic model instances.
No component writes raw SQL — use these functions instead.
"""

from __future__ import annotations

import json
import sqlite3

from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult

# ---------------------------------------------------------------------------
# Link events
# ---------------------------------------------------------------------------


def insert_link_up(conn: sqlite3.Connection, event: LinkUp) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (sim_time, wall_time, event_type, node_a, node_b,
           interface_a, interface_b, latency_ms, bandwidth_mbps, reason)
           VALUES (?, ?, 'LinkUp', ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.sim_time.isoformat(),
            event.wall_time.isoformat(),
            event.node_a,
            event.node_b,
            event.interface_a,
            event.interface_b,
            event.latency_ms,
            event.bandwidth_mbps,
            event.reason,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_link_down(conn: sqlite3.Connection, event: LinkDown) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (sim_time, wall_time, event_type, node_a, node_b,
           interface_a, interface_b, reason)
           VALUES (?, ?, 'LinkDown', ?, ?, ?, ?, ?)""",
        (
            event.sim_time.isoformat(),
            event.wall_time.isoformat(),
            event.node_a,
            event.node_b,
            event.interface_a,
            event.interface_b,
            event.reason,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_latency_update(conn: sqlite3.Connection, event: LatencyUpdate) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (sim_time, wall_time, event_type, node_a, node_b,
           interface_a, interface_b, latency_ms, range_km)
           VALUES (?, ?, 'LatencyUpdate', ?, ?, NULL, NULL, ?, ?)""",
        (
            event.sim_time.isoformat(),
            event.wall_time.isoformat(),
            event.node_a,
            event.node_b,
            event.latency_ms,
            event.range_km,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_link_event(conn: sqlite3.Connection, event: LinkUp | LinkDown) -> int:
    """Dispatch to insert_link_up or insert_link_down based on event type."""
    if isinstance(event, LinkUp):
        return insert_link_up(conn, event)
    return insert_link_down(conn, event)


def query_link_events(
    conn: sqlite3.Connection,
    start_time: str | None = None,
    end_time: str | None = None,
    node: str | None = None,
) -> list[dict]:
    """Query link events with optional time range and node filter."""
    sql = "SELECT * FROM link_events WHERE 1=1"
    params: list = []
    if start_time is not None:
        sql += " AND sim_time >= ?"
        params.append(start_time)
    if end_time is not None:
        sql += " AND sim_time <= ?"
        params.append(end_time)
    if node is not None:
        sql += " AND (node_a = ? OR node_b = ?)"
        params.extend([node, node])
    sql += " ORDER BY sim_time"
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Convergence events
# ---------------------------------------------------------------------------


def insert_convergence_result(conn: sqlite3.Connection, result: ConvergenceResult) -> int:
    cur = conn.execute(
        """INSERT INTO convergence_events (event_id, sim_time_start, sim_time_end,
           wall_time_start, wall_time_end, converged, duration_ms,
           packets_lost, packets_sent, triggering_link_event_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.event_id,
            result.sim_time_start.isoformat(),
            result.sim_time_end.isoformat(),
            result.wall_time_start.isoformat(),
            result.wall_time_end.isoformat(),
            1 if result.converged else 0,
            result.duration_ms,
            result.packets_lost,
            result.packets_sent,
            result.triggering_link_event_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_convergence_event(conn: sqlite3.Connection, result: ConvergenceResult) -> int:
    """Alias for insert_convergence_result (used by dispatcher)."""
    return insert_convergence_result(conn, result)


def query_convergence_events(
    conn: sqlite3.Connection,
    event_id: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM convergence_events WHERE 1=1"
    params: list = []
    if event_id is not None:
        sql += " AND event_id = ?"
        params.append(event_id)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Probe results
# ---------------------------------------------------------------------------


def insert_probe_result(conn: sqlite3.Connection, result: ProbeResult) -> int:
    cur = conn.execute(
        """INSERT INTO probe_results (sim_time, wall_time, flow_id, src_node, dst_node,
           packets_sent, packets_received, latency_min_ms, latency_max_ms,
           latency_avg_ms, jitter_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.sim_time.isoformat(),
            result.wall_time.isoformat(),
            result.flow_id,
            result.src_node,
            result.dst_node,
            result.packets_sent,
            result.packets_received,
            result.latency_min_ms,
            result.latency_max_ms,
            result.latency_avg_ms,
            result.jitter_ms,
        ),
    )
    conn.commit()
    return cur.lastrowid


def query_probe_results(
    conn: sqlite3.Connection,
    flow_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM probe_results WHERE 1=1"
    params: list = []
    if flow_id is not None:
        sql += " AND flow_id = ?"
        params.append(flow_id)
    if start_time is not None:
        sql += " AND sim_time >= ?"
        params.append(start_time)
    if end_time is not None:
        sql += " AND sim_time <= ?"
        params.append(end_time)
    sql += " ORDER BY sim_time"
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Adapter events
# ---------------------------------------------------------------------------


def insert_adapter_event(conn: sqlite3.Connection, event: AdapterEvent) -> int:
    cur = conn.execute(
        """INSERT INTO adapter_events (sim_time, wall_time, node_id,
           event_type, event_data)
           VALUES (?, ?, ?, ?, ?)""",
        (
            event.sim_time.isoformat(),
            event.wall_time.isoformat(),
            event.node_id,
            event.event_type,
            json.dumps(event.event_data),
        ),
    )
    conn.commit()
    return cur.lastrowid


def query_adapter_events(
    conn: sqlite3.Connection,
    node_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM adapter_events WHERE 1=1"
    params: list = []
    if node_id is not None:
        sql += " AND node_id = ?"
        params.append(node_id)
    if start_time is not None:
        sql += " AND sim_time >= ?"
        params.append(start_time)
    if end_time is not None:
        sql += " AND sim_time <= ?"
        params.append(end_time)
    sql += " ORDER BY sim_time"
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        if d.get("event_data"):
            d["event_data"] = json.loads(d["event_data"])
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Session metadata
# ---------------------------------------------------------------------------


def set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_metadata (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM session_metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Config changes
# ---------------------------------------------------------------------------


def insert_config_change(
    conn: sqlite3.Connection,
    sim_time: str,
    wall_time: str,
    change_type: str,
    description: str,
    config_snapshot: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO config_changes (sim_time, wall_time, change_type,
           description, config_snapshot)
           VALUES (?, ?, ?, ?, ?)""",
        (sim_time, wall_time, change_type, description, config_snapshot),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Snapshots (periodic full-state capture for historical playback)
# ---------------------------------------------------------------------------


def insert_snapshot(
    conn: sqlite3.Connection, sim_time: str, wall_time: str, snapshot_json: str
) -> int:
    """Store a complete StateSnapshot JSON blob."""
    cur = conn.execute(
        """INSERT INTO snapshots (sim_time, wall_time, snapshot_json)
           VALUES (?, ?, ?)""",
        (sim_time, wall_time, snapshot_json),
    )
    conn.commit()
    return cur.lastrowid


def query_nearest_snapshot(conn: sqlite3.Connection, sim_time: str) -> dict | None:
    """Return the snapshot closest to the given sim_time, or None.

    Uses two bounded queries to leverage the idx_snapshots_sim_time index
    instead of a full table scan with ABS().
    """
    conn.row_factory = sqlite3.Row

    # Closest at-or-before
    before = conn.execute(
        """SELECT sim_time, wall_time, snapshot_json FROM snapshots
           WHERE sim_time <= ? ORDER BY sim_time DESC LIMIT 1""",
        (sim_time,),
    ).fetchone()

    # Closest at-or-after
    after = conn.execute(
        """SELECT sim_time, wall_time, snapshot_json FROM snapshots
           WHERE sim_time >= ? ORDER BY sim_time ASC LIMIT 1""",
        (sim_time,),
    ).fetchone()

    if before is None and after is None:
        return None

    def _to_dict(row):
        return {
            "sim_time": row["sim_time"],
            "wall_time": row["wall_time"],
            "snapshot_json": row["snapshot_json"],
        }

    if before is None:
        return _to_dict(after)
    if after is None:
        return _to_dict(before)
    if before["sim_time"] == after["sim_time"]:
        return _to_dict(before)

    # Compare distances using julianday for precision
    dist_before = conn.execute(
        "SELECT ABS(julianday(?) - julianday(?)) AS d",
        (sim_time, before["sim_time"]),
    ).fetchone()["d"]
    dist_after = conn.execute(
        "SELECT ABS(julianday(?) - julianday(?)) AS d",
        (sim_time, after["sim_time"]),
    ).fetchone()["d"]

    return _to_dict(before) if dist_before <= dist_after else _to_dict(after)
