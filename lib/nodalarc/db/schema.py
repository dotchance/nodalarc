"""SQLite schema for Nodal Arc event storage.

All 6 tables + indexes. WAL mode enabled for concurrent reads.
Column names match Pydantic model field names for consistency.
"""

import sqlite3

DDL_LINK_EVENTS = """
CREATE TABLE IF NOT EXISTS link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_a TEXT NOT NULL,
    node_b TEXT NOT NULL,
    interface_a TEXT NOT NULL,
    interface_b TEXT NOT NULL,
    latency_ms REAL,
    bandwidth_mbps REAL,
    range_km REAL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_CONVERGENCE_EVENTS = """
CREATE TABLE IF NOT EXISTS convergence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    converged INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    packets_lost INTEGER NOT NULL,
    packets_sent INTEGER NOT NULL,
    sim_time_start TEXT NOT NULL,
    sim_time_end TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_PROBE_RESULTS = """
CREATE TABLE IF NOT EXISTS probe_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    jitter_ms REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_ADAPTER_EVENTS = """
CREATE TABLE IF NOT EXISTS adapter_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_time TEXT NOT NULL,
    wall_time TEXT NOT NULL,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_SESSION_METADATA = """
CREATE TABLE IF NOT EXISTS session_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_CONFIG_CHANGES = """
CREATE TABLE IF NOT EXISTS config_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_s REAL NOT NULL,
    node_id TEXT NOT NULL,
    config_type TEXT NOT NULL,
    old_hash TEXT,
    new_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_link_events_sim_time ON link_events(sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_link_events_nodes ON link_events(node_a, node_b);",
    "CREATE INDEX IF NOT EXISTS idx_convergence_event_id ON convergence_events(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_probe_results_sim_time ON probe_results(sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_probe_results_flow ON probe_results(flow_id);",
    "CREATE INDEX IF NOT EXISTS idx_adapter_events_sim_time ON adapter_events(sim_time);",
    "CREATE INDEX IF NOT EXISTS idx_adapter_events_node ON adapter_events(node_id);",
    "CREATE INDEX IF NOT EXISTS idx_config_changes_timestamp ON config_changes(timestamp_s);",
    "CREATE INDEX IF NOT EXISTS idx_config_changes_node ON config_changes(node_id);",
]

ALL_DDL = [
    DDL_LINK_EVENTS,
    DDL_CONVERGENCE_EVENTS,
    DDL_PROBE_RESULTS,
    DDL_ADAPTER_EVENTS,
    DDL_SESSION_METADATA,
    DDL_CONFIG_CHANGES,
]


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes. Enable WAL mode."""
    conn.execute("PRAGMA journal_mode=WAL;")
    for ddl in ALL_DDL:
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()
