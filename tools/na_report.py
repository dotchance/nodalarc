"""na-report — single-session report tool.

Generates reports from one recorded session's history database. Distinct from
na-compare, which compares two or more sessions.

Usage:
  python -m tools.na_report --db /path/to/nodalarc.db --report summary
  python -m tools.na_report --db /path/to/nodalarc.db --report convergence
  python -m tools.na_report --db /path/to/nodalarc.db --report link-events
  python -m tools.na_report --db /path/to/nodalarc.db --report probe-results
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from statistics import median

from nodal.logging import configure as _configure_logging
from nodalarc.db.queries import (
    get_metadata,
    query_convergence_events,
    query_link_events,
    query_probe_results,
    recorded_session_id,
)
from nodalarc.db.schema import require_schema_version

log = logging.getLogger(__name__)

REPORT_TYPES = ["summary", "convergence", "link-events", "probe-results"]

TABLES = [
    "link_events",
    "convergence_events",
    "probe_results",
    "adapter_events",
    "session_metadata",
    "snapshots",
]

# What VS-API records for a session when it opens the session's history file.
METADATA_KEYS = [
    "session_name",
    "routing_stack",
    "source_id",
    "start_time",
]


def _open_db(path: str) -> sqlite3.Connection:
    """Open a history database read-only, refusing another schema version."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    require_schema_version(conn)
    return conn


def _count_table(conn: sqlite3.Connection, session_id: str, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,))
    return row.fetchone()[0]


def _time_range(
    conn: sqlite3.Connection, session_id: str, table: str, col: str = "sim_time"
) -> tuple[str | None, str | None]:
    """Min and max of a time column, (None, None) for a session with no rows."""
    row = conn.execute(
        f"SELECT MIN({col}), MAX({col}) FROM {table} WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row[0], row[1]


def report_summary(conn: sqlite3.Connection, session_id: str) -> str:
    """Session metadata, row counts, and time range."""
    lines = []
    lines.append("=" * 60)
    lines.append("SESSION REPORT — SUMMARY")
    lines.append("=" * 60)
    lines.append("")

    # Metadata
    lines.append(f"Session: {session_id}")
    lines.append("")
    lines.append("Metadata:")
    lines.append("-" * 40)
    has_meta = False
    for key in METADATA_KEYS:
        val = get_metadata(conn, session_id=session_id, key=key)
        if val is not None:
            lines.append(f"  {key:<24} {val}")
            has_meta = True
    if not has_meta:
        lines.append("  (no metadata)")
    lines.append("")

    # Table counts
    lines.append("Table row counts:")
    lines.append("-" * 40)
    for table in TABLES:
        cnt = _count_table(conn, session_id, table)
        lines.append(f"  {table:<24} {cnt:>8}")
    lines.append("")

    # Time range from link_events
    t_min, t_max = _time_range(conn, session_id, "link_events")
    if t_min is not None:
        lines.append(f"Event time range: {t_min} — {t_max}")

    return "\n".join(lines)


def report_convergence(conn: sqlite3.Connection, session_id: str) -> str:
    """All convergence events with statistics."""
    lines = []
    lines.append("=" * 60)
    lines.append("SESSION REPORT — CONVERGENCE EVENTS")
    lines.append("=" * 60)
    lines.append("")

    events = query_convergence_events(conn, session_id=session_id)

    if not events:
        lines.append("(no convergence events)")
        return "\n".join(lines)

    header = (
        f"{'event_id':<20} {'converged':<10} {'duration_ms':>12} "
        f"{'pkts_lost':>10} {'pkts_sent':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    durations = []
    total_lost = 0
    for e in events:
        converged = "yes" if e["converged"] else "no"
        dur = e["duration_ms"]
        lost = e["packets_lost"]
        sent = e["packets_sent"]
        lines.append(f"{e['event_id']:<20} {converged:<10} {dur:>12.1f} {lost:>10} {sent:>10}")
        durations.append(dur)
        total_lost += lost

    lines.append("")
    lines.append("Statistics:")
    lines.append("-" * 40)
    if durations:
        sorted_d = sorted(durations)
        mean = sum(sorted_d) / len(sorted_d)
        median_duration = median(sorted_d)
        lines.append(f"  Events:           {len(durations)}")
        lines.append(f"  Mean duration:    {mean:.1f} ms")
        lines.append(f"  Median duration:  {median_duration:.1f} ms")
        lines.append(f"  Max duration:     {max(sorted_d):.1f} ms")
        lines.append(f"  Total pkt loss:   {total_lost}")

    return "\n".join(lines)


def report_link_events(conn: sqlite3.Connection, session_id: str) -> str:
    """Link event timeline with type counts and per-node churn."""
    lines = []
    lines.append("=" * 60)
    lines.append("SESSION REPORT — LINK EVENTS")
    lines.append("=" * 60)
    lines.append("")

    events = query_link_events(conn, session_id=session_id)

    if not events:
        lines.append("(no link events)")
        return "\n".join(lines)

    # Timeline
    header = f"{'sim_time':<26} {'type':<16} {'node_a':<14} {'node_b':<14} {'latency_ms':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for e in events:
        lat = f"{e['latency_ms']:.1f}" if e.get("latency_ms") is not None else "-"
        lines.append(
            f"{e['sim_time']:<26} {e['event_type']:<16} "
            f"{e['node_a']:<14} {e['node_b']:<14} {lat:>10}"
        )
    lines.append("")

    # Event counts by type
    type_counts: dict[str, int] = {}
    node_churn: dict[str, int] = {}
    for e in events:
        etype = e["event_type"]
        type_counts[etype] = type_counts.get(etype, 0) + 1
        if etype in ("LinkUp", "LinkDown"):
            for node in (e["node_a"], e["node_b"]):
                node_churn[node] = node_churn.get(node, 0) + 1

    lines.append("Event counts by type:")
    lines.append("-" * 40)
    for etype in ["LinkUp", "LinkDown", "LatencyUpdate"]:
        cnt = type_counts.get(etype, 0)
        lines.append(f"  {etype:<20} {cnt:>8}")
    lines.append("")

    # Per-node adjacency churn
    if node_churn:
        lines.append("Per-node adjacency churn (ups + downs):")
        lines.append("-" * 40)
        for node in sorted(node_churn):
            lines.append(f"  {node:<24} {node_churn[node]:>8}")

    return "\n".join(lines)


def report_probe_results(conn: sqlite3.Connection, session_id: str) -> str:
    """Per-flow probe statistics."""
    lines = []
    lines.append("=" * 60)
    lines.append("SESSION REPORT — PROBE RESULTS")
    lines.append("=" * 60)
    lines.append("")

    all_results = query_probe_results(conn, session_id=session_id)

    if not all_results:
        lines.append("(no probe results)")
        return "\n".join(lines)

    # Group by flow_id
    flows: dict[str, list[dict]] = {}
    for r in all_results:
        fid = r["flow_id"]
        flows.setdefault(fid, []).append(r)

    header = (
        f"{'flow_id':<20} {'samples':>8} {'sent':>8} {'recv':>8} "
        f"{'loss%':>7} {'lat_min':>8} {'lat_avg':>8} {'lat_max':>8} {'jitter':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for fid in sorted(flows):
        rows = flows[fid]
        total_sent = sum(r["packets_sent"] for r in rows)
        total_recv = sum(r["packets_received"] for r in rows)
        loss_pct = ((total_sent - total_recv) / total_sent * 100) if total_sent > 0 else 0.0
        lat_min = min(r["latency_min_ms"] for r in rows)
        lat_max = max(r["latency_max_ms"] for r in rows)
        lat_avg = sum(r["latency_avg_ms"] for r in rows) / len(rows)
        jitter_avg = sum(r["jitter_ms"] for r in rows) / len(rows)

        lines.append(
            f"{fid:<20} {len(rows):>8} {total_sent:>8} {total_recv:>8} "
            f"{loss_pct:>6.1f}% {lat_min:>8.1f} {lat_avg:>8.1f} "
            f"{lat_max:>8.1f} {jitter_avg:>8.1f}"
        )

    return "\n".join(lines)


def run_report(db_path: str, report_type: str) -> str:
    """Run a report on a single session database."""
    if not Path(db_path).exists():
        log.error(f"Database not found: {db_path}")
        sys.exit(1)

    conn = _open_db(db_path)
    try:
        session_id = recorded_session_id(conn)
        match report_type:
            case "summary":
                return report_summary(conn, session_id)
            case "convergence":
                return report_convergence(conn, session_id)
            case "link-events":
                return report_link_events(conn, session_id)
            case "probe-results":
                return report_probe_results(conn, session_id)
            case _:
                log.error(f"Unknown report type: {report_type}")
                sys.exit(1)
    finally:
        conn.close()


def main() -> None:
    _configure_logging("nodal.arc.tools.na_report", nats_level=None, stream=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Nodal Arc Single-Session Report Tool",
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="Path to session SQLite database",
    )
    parser.add_argument(
        "--report",
        choices=REPORT_TYPES,
        default="summary",
        help="Report type (default: summary)",
    )
    args = parser.parse_args()

    output = run_report(args.db, args.report)
    print(output)


if __name__ == "__main__":
    main()
