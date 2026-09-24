# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed insert/query functions for the session history database.

Every row belongs to one session run: writers take the session id and readers
return one session's rows. No component writes raw SQL; use these functions.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult


def recorded_session_id(conn: sqlite3.Connection, schema: str = "main") -> str:
    """The run id of the one session a history file records."""
    rows = conn.execute(f"SELECT DISTINCT session_id FROM {schema}.session_metadata").fetchall()
    if len(rows) != 1:
        raise ValueError(f"history database records {len(rows)} sessions; expected exactly one")
    return rows[0][0]


# ---------------------------------------------------------------------------
# Link events
# ---------------------------------------------------------------------------


def insert_link_up(conn: sqlite3.Connection, event: LinkUp, *, session_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (session_id, sim_time, wall_time, event_type, node_a,
           node_b, interface_a, interface_b, latency_ms, range_km, reason)
           VALUES (?, ?, ?, 'LinkUp', ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            event.sim_time.isoformat(),
            event.wall_time.isoformat(),
            event.node_a,
            event.node_b,
            event.interface_a,
            event.interface_b,
            event.latency_ms,
            event.range_km,
            event.reason,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_link_down(conn: sqlite3.Connection, event: LinkDown, *, session_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (session_id, sim_time, wall_time, event_type, node_a,
           node_b, interface_a, interface_b, reason)
           VALUES (?, ?, ?, 'LinkDown', ?, ?, ?, ?, ?)""",
        (
            session_id,
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


def insert_latency_update(
    conn: sqlite3.Connection, event: LatencyUpdate, *, session_id: str
) -> int:
    cur = conn.execute(
        """INSERT INTO link_events (session_id, sim_time, wall_time, event_type, node_a,
           node_b, interface_a, interface_b, latency_ms, range_km)
           VALUES (?, ?, ?, 'LatencyUpdate', ?, ?, NULL, NULL, ?, ?)""",
        (
            session_id,
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


def insert_active_links(
    conn: sqlite3.Connection,
    pairs: Iterable[tuple[str, str]],
    *,
    session_id: str,
    sim_time: datetime,
    wall_time: datetime,
) -> None:
    """Record the kernel-actual links at the start of a session's recording.

    LinkUp rows exist only from the moment recording subscribes, so a
    recording opens with one LinkActive row per link the Scheduler had
    already proven up, reason recording_start. Interfaces, latency and range
    are not part of the kernel-actual set and stay empty.
    """
    rows = [
        (session_id, sim_time.isoformat(), wall_time.isoformat(), node_a, node_b)
        for node_a, node_b in pairs
    ]
    conn.executemany(
        """INSERT INTO link_events (session_id, sim_time, wall_time, event_type, node_a,
           node_b, reason)
           VALUES (?, ?, ?, 'LinkActive', ?, ?, 'recording_start')""",
        rows,
    )
    conn.commit()


def _link_event_filter(
    session_id: str,
    start_time: str | None,
    end_time: str | None,
    node: str | None,
    peer: str | None,
) -> tuple[str, list]:
    where = "session_id = ?"
    params: list = [session_id]
    if start_time is not None:
        where += " AND sim_time >= ?"
        params.append(start_time)
    if end_time is not None:
        where += " AND sim_time <= ?"
        params.append(end_time)
    if peer is not None:
        if node is None:
            raise ValueError("a peer filter names the other end of a node's link; name the node")
        where += " AND ((node_a = ? AND node_b = ?) OR (node_a = ? AND node_b = ?))"
        params.extend([node, peer, peer, node])
    elif node is not None:
        where += " AND (node_a = ? OR node_b = ?)"
        params.extend([node, node])
    return where, params


def query_link_events(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    node: str | None = None,
    peer: str | None = None,
    newest_first: bool = False,
    after: tuple[str, int] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """One session's link events in (sim_time, id) order.

    Optional filters: a sim-time range, one node's links, or the one link
    between ``node`` and ``peer``. ``after`` is the (sim_time, id) of a row
    already read; the rows returned follow it in the chosen order. ``limit``
    bounds how many rows are returned.
    """
    where, params = _link_event_filter(session_id, start_time, end_time, node, peer)
    if after is not None:
        beyond = "<" if newest_first else ">"
        where += f" AND (sim_time {beyond} ? OR (sim_time = ? AND id {beyond} ?))"
        params.extend([after[0], after[0], after[1]])
    direction = "DESC" if newest_first else "ASC"
    sql = f"SELECT * FROM link_events WHERE {where} ORDER BY sim_time {direction}, id {direction}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def count_link_events(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    node: str | None = None,
    peer: str | None = None,
) -> int:
    """How many of one session's link events match the filters of ``query_link_events``."""
    where, params = _link_event_filter(session_id, start_time, end_time, node, peer)
    return conn.execute(f"SELECT count(*) FROM link_events WHERE {where}", params).fetchone()[0]


# ---------------------------------------------------------------------------
# Convergence events
# ---------------------------------------------------------------------------


def insert_convergence_result(
    conn: sqlite3.Connection, result: ConvergenceResult, *, session_id: str
) -> int:
    cur = conn.execute(
        """INSERT INTO convergence_events (session_id, event_id, sim_time_start,
           sim_time_end, wall_time_start, wall_time_end, converged, duration_ms,
           packets_lost, packets_sent, triggering_link_event_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
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


def query_convergence_events(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    event_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM convergence_events WHERE session_id = ?"
    params: list = [session_id]
    if event_id is not None:
        sql += " AND event_id = ?"
        params.append(event_id)
    if start_time is not None:
        sql += " AND sim_time_start >= ?"
        params.append(start_time)
    if end_time is not None:
        sql += " AND sim_time_start <= ?"
        params.append(end_time)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Probe results
# ---------------------------------------------------------------------------


def insert_probe_result(conn: sqlite3.Connection, result: ProbeResult, *, session_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO probe_results (session_id, sim_time, wall_time, flow_id, src_node,
           dst_node, packets_sent, packets_received, latency_min_ms, latency_max_ms,
           latency_avg_ms, jitter_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
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
    *,
    session_id: str,
    flow_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM probe_results WHERE session_id = ?"
    params: list = [session_id]
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


def insert_adapter_event(conn: sqlite3.Connection, event: AdapterEvent, *, session_id: str) -> int:
    cur = conn.execute(
        """INSERT INTO adapter_events (session_id, sim_time, wall_time, node_id,
           event_type, event_data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            session_id,
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
    *,
    session_id: str,
    node_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM adapter_events WHERE session_id = ?"
    params: list = [session_id]
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


def set_metadata(conn: sqlite3.Connection, *, session_id: str, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_metadata (session_id, key, value) VALUES (?, ?, ?)",
        (session_id, key, value),
    )
    conn.commit()


def get_metadata(conn: sqlite3.Connection, *, session_id: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM session_metadata WHERE session_id = ? AND key = ?",
        (session_id, key),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Snapshots (periodic full-state capture for historical playback)
# ---------------------------------------------------------------------------


def insert_snapshot(
    conn: sqlite3.Connection, *, session_id: str, sim_time: str, wall_time: str, snapshot_json: str
) -> int:
    """Store a complete StateSnapshot JSON blob."""
    cur = conn.execute(
        """INSERT INTO snapshots (session_id, sim_time, wall_time, snapshot_json)
           VALUES (?, ?, ?, ?)""",
        (session_id, sim_time, wall_time, snapshot_json),
    )
    conn.commit()
    return cur.lastrowid


def query_nearest_snapshot(
    conn: sqlite3.Connection, *, session_id: str, sim_time: str
) -> dict | None:
    """Return the session's snapshot closest to the given sim_time, or None.

    Uses two bounded queries to leverage the idx_snapshots_sim_time index
    instead of a full table scan with ABS().
    """
    conn.row_factory = sqlite3.Row

    # Closest at-or-before
    before = conn.execute(
        """SELECT sim_time, wall_time, snapshot_json FROM snapshots
           WHERE session_id = ? AND sim_time <= ? ORDER BY sim_time DESC LIMIT 1""",
        (session_id, sim_time),
    ).fetchone()

    # Closest at-or-after
    after = conn.execute(
        """SELECT sim_time, wall_time, snapshot_json FROM snapshots
           WHERE session_id = ? AND sim_time >= ? ORDER BY sim_time ASC LIMIT 1""",
        (session_id, sim_time),
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


# ---------------------------------------------------------------------------
# OME lifecycle events and operator interventions
# ---------------------------------------------------------------------------


def _required(mapping: dict, key: str, what: str):
    """A field the persisted record requires; absence refuses the record."""
    value = mapping.get(key)
    if value is None or value == "":
        raise ValueError(f"{what} is missing required field {key!r}")
    return value


def _require_session(event: dict, session_id: str, what: str) -> None:
    """An event from another session never enters this session's history."""
    event_session = _required(event, "session_id", what)
    if event_session != session_id:
        raise ValueError(f"{what} belongs to session {event_session!r}, not {session_id!r}")


def insert_ome_lifecycle_event(conn: sqlite3.Connection, event: dict, *, session_id: str) -> int:
    """Persist one OME terminal-lifecycle OpsEvent (MBB_TEARDOWN_TERMINAL)."""
    what = "OME lifecycle event"
    if event.get("source") != "ome" or event.get("code") != "MBB_TEARDOWN_TERMINAL":
        raise ValueError(f"{what} must be an OME MBB_TEARDOWN_TERMINAL event")
    _require_session(event, session_id, what)
    details = _required(event, "details", what)
    cur = conn.execute(
        """INSERT INTO ome_lifecycle_events (
               session_id, epoch_id, snapshot_seq, allocator_step, sim_time, event_time,
               gs_id, old_pair, successor_pair, terminal_outcome, event_code, event_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            int(_required(details, "epoch_id", what)),
            details.get("snapshot_seq"),
            int(_required(details, "allocator_step", what)),
            _required(details, "master_sim_time", what),
            _required(event, "timestamp", what),
            _required(details, "gs_id", what),
            json.dumps(_required(details, "old_pair", what), sort_keys=True),
            json.dumps(_required(details, "successor_pair", what), sort_keys=True),
            _required(details, "terminal_outcome", what),
            event["code"],
            json.dumps(event, sort_keys=True),
        ),
    )
    conn.commit()
    return cur.lastrowid


def query_ome_lifecycle_events(conn: sqlite3.Connection, *, session_id: str) -> list[dict]:
    """One session's persisted OME lifecycle events in emission order."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ome_lifecycle_events WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_operator_intervention_event(
    conn: sqlite3.Connection, event: dict, *, session_id: str
) -> int:
    """Append one durable causal event for an operator intervention.

    Operator repair is deliberately not invisible self-healing. Later analysis
    must be able to reconstruct the full chain: request, authority snapshot,
    repair mutations, verification, and final state. Therefore this is
    append-only and also marks the session as intervened in metadata.
    """
    what = "operator intervention event"
    _require_session(event, session_id, what)
    details = _required(event, "details", what)
    status = _required(event, "code", what)
    cur = conn.execute(
        """INSERT INTO operator_interventions (
               intervention_id, session_id, wiring_generation, scheduler_instance_id,
               hostname, gs_id, status, reason, event_time, event_code, event_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _required(details, "intervention_id", what),
            session_id,
            _required(details, "wiring_generation", what),
            _required(details, "scheduler_instance_id", what),
            _required(event, "hostname", what),
            _required(details, "gs_id", what),
            status,
            details.get("reason"),
            _required(event, "timestamp", what),
            status,
            json.dumps(event, sort_keys=True),
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO session_metadata (session_id, key, value) VALUES (?, ?, ?)",
        (session_id, "operator_intervened", "true"),
    )
    conn.commit()
    return cur.lastrowid
