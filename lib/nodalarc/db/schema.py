# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""SQLite schema for the session history database.

VS-API owns one database file for every session's history; each row carries
the run id of the session it belongs to. WAL mode serves concurrent reads.
Column names match Pydantic model field names.

PRAGMA user_version records SCHEMA_VERSION. A database at another version was
written by another release; it is refused, never migrated.
"""

import sqlite3

SCHEMA_VERSION = 1


class HistorySchemaError(RuntimeError):
    """The database holds tables from a schema version this release does not write."""


DDL_LINK_EVENTS = """
CREATE TABLE IF NOT EXISTS link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_a TEXT NOT NULL,
    node_b TEXT NOT NULL,
    interface_a TEXT,
    interface_b TEXT,
    latency_ms REAL,
    range_km REAL,
    reason TEXT
);
"""

DDL_CONVERGENCE_EVENTS = """
CREATE TABLE IF NOT EXISTS convergence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    sim_time_start TEXT NOT NULL,
    sim_time_end TEXT NOT NULL,
    wall_time_start TEXT NOT NULL,
    wall_time_end TEXT NOT NULL,
    converged INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    packets_lost INTEGER NOT NULL,
    packets_sent INTEGER NOT NULL,
    triggering_link_event_id INTEGER REFERENCES link_events(id)
);
"""

DDL_PROBE_RESULTS = """
CREATE TABLE IF NOT EXISTS probe_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    flow_id TEXT NOT NULL,
    src_node TEXT NOT NULL,
    dst_node TEXT NOT NULL,
    packets_sent INTEGER NOT NULL,
    packets_received INTEGER NOT NULL,
    latency_min_ms REAL NOT NULL,
    latency_max_ms REAL NOT NULL,
    latency_avg_ms REAL NOT NULL,
    jitter_ms REAL NOT NULL
);
"""

DDL_ADAPTER_EVENTS = """
CREATE TABLE IF NOT EXISTS adapter_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL
);
"""

DDL_SESSION_METADATA = """
CREATE TABLE IF NOT EXISTS session_metadata (
    session_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (session_id, key)
);
"""

DDL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
"""


DDL_OME_LIFECYCLE_EVENTS = """
CREATE TABLE IF NOT EXISTS ome_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    epoch_id INTEGER NOT NULL,
    snapshot_seq INTEGER,
    allocator_step INTEGER NOT NULL,
    sim_time TEXT NOT NULL,
    event_time TEXT NOT NULL,
    gs_id TEXT NOT NULL,
    old_pair TEXT NOT NULL,
    successor_pair TEXT NOT NULL,
    terminal_outcome TEXT NOT NULL,
    event_code TEXT NOT NULL,
    event_json TEXT NOT NULL
);
"""

DDL_OPERATOR_INTERVENTIONS = """
CREATE TABLE IF NOT EXISTS operator_interventions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intervention_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    wiring_generation TEXT NOT NULL,
    scheduler_instance_id TEXT NOT NULL,
    hostname TEXT NOT NULL,
    gs_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    event_time TEXT NOT NULL,
    event_code TEXT NOT NULL,
    event_json TEXT NOT NULL
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_link_events_time ON link_events(session_id, sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_link_events_nodes ON link_events(session_id, node_a, node_b);",
    "CREATE INDEX IF NOT EXISTS idx_convergence_time"
    " ON convergence_events(session_id, sim_time_start);",
    "CREATE INDEX IF NOT EXISTS idx_probe_results_time ON probe_results(session_id, sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_probe_results_flow ON probe_results(session_id, flow_id);",
    "CREATE INDEX IF NOT EXISTS idx_adapter_events_time ON adapter_events(session_id, sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_adapter_events_node ON adapter_events(session_id, node_id);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(session_id, sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_ome_lifecycle_session ON ome_lifecycle_events(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_ome_lifecycle_pair ON ome_lifecycle_events(session_id, old_pair, successor_pair);",
    "CREATE INDEX IF NOT EXISTS idx_ome_lifecycle_outcome ON ome_lifecycle_events(terminal_outcome);",
    "CREATE INDEX IF NOT EXISTS idx_operator_interventions_session ON operator_interventions(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_operator_interventions_id ON operator_interventions(intervention_id);",
    "CREATE INDEX IF NOT EXISTS idx_operator_interventions_gs ON operator_interventions(gs_id);",
]

ALL_DDL = [
    DDL_LINK_EVENTS,
    DDL_CONVERGENCE_EVENTS,
    DDL_PROBE_RESULTS,
    DDL_ADAPTER_EVENTS,
    DDL_SESSION_METADATA,
    DDL_SNAPSHOTS,
    DDL_OME_LIFECYCLE_EVENTS,
    DDL_OPERATOR_INTERVENTIONS,
]


def require_schema_version(conn: sqlite3.Connection, schema: str = "main") -> None:
    """Refuse a history database written at another schema version."""
    version = conn.execute(f"PRAGMA {schema}.user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise HistorySchemaError(
            f"history database schema version {version} is not {SCHEMA_VERSION}: another "
            "NodalArc release wrote it"
        )


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the schema in an empty database, or accept one at SCHEMA_VERSION.

    Raises HistorySchemaError when the database already holds tables at
    another version.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    if tables and version != SCHEMA_VERSION:
        raise HistorySchemaError(
            f"history database schema version {version} is not {SCHEMA_VERSION}: another "
            "NodalArc release wrote it; remove the database file so this release creates it"
        )
    conn.execute("PRAGMA journal_mode=WAL;")
    for ddl in ALL_DDL:
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
