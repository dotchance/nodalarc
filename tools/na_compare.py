"""na-compare — cross-session analysis tool.

PRD Section 9: query and compare SQLite databases from different sessions,
putting two routing stack runs side by side and seeing where they diverged.

Usage:
  python -m tools.na_compare --sessions session1/nodalarc.db session2/nodalarc.db --report summary
  python -m tools.na_compare --sessions db1 db2 --report convergence
  python -m tools.na_compare --sessions db1 db2 --report link-events
  python -m tools.na_compare --sessions db1 db2 --report probe-results
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from nodalarc.constants import LOG_FORMAT

log = logging.getLogger(__name__)

REPORT_TYPES = ["summary", "convergence", "link-events", "probe-results"]


def _attach_databases(paths: list[str]) -> sqlite3.Connection:
    """Open an in-memory connection and attach each session DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for i, path in enumerate(paths):
        alias = f"s{i + 1}"
        conn.execute(f"ATTACH DATABASE ? AS {alias}", (path,))
    return conn


def _get_metadata(conn: sqlite3.Connection, alias: str) -> dict[str, str]:
    """Read session_metadata from an attached database."""
    try:
        rows = conn.execute(f"SELECT key, value FROM {alias}.session_metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}
    except sqlite3.OperationalError:
        return {}


def _count_table(conn: sqlite3.Connection, alias: str, table: str) -> int:
    """Count rows in a table, returning 0 if the table doesn't exist."""
    try:
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {alias}.{table}").fetchone()
        return row["cnt"]
    except sqlite3.OperationalError:
        return 0


def report_summary(conn: sqlite3.Connection, aliases: list[str]) -> str:
    """Session metadata and event counts side by side."""
    lines = []
    lines.append("=" * 72)
    lines.append("SESSION COMPARISON — SUMMARY")
    lines.append("=" * 72)
    lines.append("")

    # Metadata
    all_meta = {a: _get_metadata(conn, a) for a in aliases}
    all_keys = sorted({k for m in all_meta.values() for k in m})

    if all_keys:
        header = f"{'Key':<30}" + "".join(f"  {a:<18}" for a in aliases)
        lines.append(header)
        lines.append("-" * len(header))
        for key in all_keys:
            row = f"{key:<30}"
            for a in aliases:
                val = all_meta[a].get(key, "-")
                row += f"  {val:<18}"
            lines.append(row)
        lines.append("")

    # Table counts
    tables = ["link_events", "convergence_events", "probe_results", "adapter_events", "snapshots"]
    header = f"{'Table':<30}" + "".join(f"  {a:<18}" for a in aliases)
    lines.append(header)
    lines.append("-" * len(header))
    for table in tables:
        row = f"{table:<30}"
        for a in aliases:
            cnt = _count_table(conn, a, table)
            row += f"  {cnt:<18}"
        lines.append(row)

    return "\n".join(lines)


def report_convergence(conn: sqlite3.Connection, aliases: list[str]) -> str:
    """Compare convergence events across sessions."""
    lines = []
    lines.append("=" * 72)
    lines.append("SESSION COMPARISON — CONVERGENCE EVENTS")
    lines.append("=" * 72)
    lines.append("")

    for a in aliases:
        lines.append(f"--- {a} ---")
        try:
            rows = conn.execute(
                f"""SELECT event_id, sim_time_start, converged, duration_ms,
                           packets_lost, packets_sent
                    FROM {a}.convergence_events ORDER BY sim_time_start"""
            ).fetchall()
        except sqlite3.OperationalError:
            lines.append("  (no convergence_events table)")
            lines.append("")
            continue

        if not rows:
            lines.append("  (no events)")
            lines.append("")
            continue

        header = f"  {'event_id':<20} {'converged':<10} {'duration_ms':>12} {'pkts_lost':>10} {'pkts_sent':>10}"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for row in rows:
            converged = "yes" if row["converged"] else "no"
            lines.append(
                f"  {row['event_id']:<20} {converged:<10} "
                f"{row['duration_ms']:>12.1f} {row['packets_lost']:>10} "
                f"{row['packets_sent']:>10}"
            )
        lines.append("")

    # Side-by-side comparison if exactly 2 sessions share event_ids
    if len(aliases) == 2:
        a1, a2 = aliases
        try:
            rows = conn.execute(
                f"""SELECT s1.event_id,
                           s1.duration_ms AS s1_ms, s2.duration_ms AS s2_ms,
                           s1.packets_lost AS s1_lost, s2.packets_lost AS s2_lost,
                           s1.converged AS s1_conv, s2.converged AS s2_conv
                    FROM {a1}.convergence_events s1
                    JOIN {a2}.convergence_events s2 ON s1.event_id = s2.event_id
                    ORDER BY s1.event_id"""
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if rows:
            lines.append("--- Side-by-side (matched event_id) ---")
            header = (
                f"  {'event_id':<20} "
                f"{'s1_ms':>10} {'s2_ms':>10} {'delta_ms':>10}   "
                f"{'s1_lost':>8} {'s2_lost':>8}"
            )
            lines.append(header)
            lines.append("  " + "-" * (len(header) - 2))
            for row in rows:
                delta = row["s2_ms"] - row["s1_ms"]
                lines.append(
                    f"  {row['event_id']:<20} "
                    f"{row['s1_ms']:>10.1f} {row['s2_ms']:>10.1f} "
                    f"{delta:>+10.1f}   "
                    f"{row['s1_lost']:>8} {row['s2_lost']:>8}"
                )

    return "\n".join(lines)


def report_link_events(conn: sqlite3.Connection, aliases: list[str]) -> str:
    """Compare link event timelines across sessions."""
    lines = []
    lines.append("=" * 72)
    lines.append("SESSION COMPARISON — LINK EVENTS")
    lines.append("=" * 72)
    lines.append("")

    for a in aliases:
        lines.append(f"--- {a} ---")
        try:
            rows = conn.execute(
                f"""SELECT sim_time, event_type, node_a, node_b, latency_ms, reason
                    FROM {a}.link_events ORDER BY sim_time"""
            ).fetchall()
        except sqlite3.OperationalError:
            lines.append("  (no link_events table)")
            lines.append("")
            continue

        if not rows:
            lines.append("  (no events)")
            lines.append("")
            continue

        header = f"  {'sim_time':<26} {'type':<16} {'node_a':<14} {'node_b':<14} {'latency_ms':>10} {'reason'}"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for row in rows:
            lat = f"{row['latency_ms']:.1f}" if row["latency_ms"] is not None else "-"
            reason = row["reason"] or "-"
            lines.append(
                f"  {row['sim_time']:<26} {row['event_type']:<16} "
                f"{row['node_a']:<14} {row['node_b']:<14} "
                f"{lat:>10} {reason}"
            )
        lines.append("")

    # Count summary
    lines.append("--- Event count by type ---")
    header = f"  {'event_type':<20}" + "".join(f"  {a:<10}" for a in aliases)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for etype in ["LinkUp", "LinkDown", "LatencyUpdate"]:
        row = f"  {etype:<20}"
        for a in aliases:
            try:
                cnt = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM {a}.link_events WHERE event_type = ?",
                    (etype,),
                ).fetchone()["cnt"]
            except sqlite3.OperationalError:
                cnt = 0
            row += f"  {cnt:<10}"
        lines.append(row)

    return "\n".join(lines)


def report_probe_results(conn: sqlite3.Connection, aliases: list[str]) -> str:
    """Compare probe results across sessions."""
    lines = []
    lines.append("=" * 72)
    lines.append("SESSION COMPARISON — PROBE RESULTS")
    lines.append("=" * 72)
    lines.append("")

    for a in aliases:
        lines.append(f"--- {a} ---")
        try:
            rows = conn.execute(
                f"""SELECT flow_id,
                           COUNT(*) AS samples,
                           SUM(packets_sent) AS total_sent,
                           SUM(packets_received) AS total_recv,
                           AVG(latency_avg_ms) AS avg_lat,
                           MIN(latency_min_ms) AS min_lat,
                           MAX(latency_max_ms) AS max_lat,
                           AVG(jitter_ms) AS avg_jitter
                    FROM {a}.probe_results
                    GROUP BY flow_id
                    ORDER BY flow_id"""
            ).fetchall()
        except sqlite3.OperationalError:
            lines.append("  (no probe_results table)")
            lines.append("")
            continue

        if not rows:
            lines.append("  (no probe data)")
            lines.append("")
            continue

        header = (
            f"  {'flow_id':<20} {'samples':>8} {'sent':>8} {'recv':>8} "
            f"{'loss%':>7} {'avg_lat':>8} {'jitter':>8}"
        )
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for row in rows:
            total_sent = row["total_sent"] or 0
            total_recv = row["total_recv"] or 0
            loss_pct = ((total_sent - total_recv) / total_sent * 100) if total_sent > 0 else 0.0
            lines.append(
                f"  {row['flow_id']:<20} {row['samples']:>8} "
                f"{total_sent:>8} {total_recv:>8} "
                f"{loss_pct:>6.1f}% {row['avg_lat']:>8.1f} {row['avg_jitter']:>8.1f}"
            )
        lines.append("")

    return "\n".join(lines)


def run_compare(session_paths: list[str], report_type: str) -> str:
    """Run a comparison report across multiple session databases."""
    for path in session_paths:
        if not Path(path).exists():
            log.error(f"Database not found: {path}")
            sys.exit(1)

    conn = _attach_databases(session_paths)
    aliases = [f"s{i + 1}" for i in range(len(session_paths))]

    match report_type:
        case "summary":
            return report_summary(conn, aliases)
        case "convergence":
            return report_convergence(conn, aliases)
        case "link-events":
            return report_link_events(conn, aliases)
        case "probe-results":
            return report_probe_results(conn, aliases)
        case _:
            log.error(f"Unknown report type: {report_type}")
            sys.exit(1)


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Nodal Arc Cross-Session Comparison Tool",
    )
    parser.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        metavar="DB",
        help="Paths to session SQLite databases (min 2)",
    )
    parser.add_argument(
        "--report",
        choices=REPORT_TYPES,
        default="summary",
        help="Report type (default: summary)",
    )
    args = parser.parse_args()

    if len(args.sessions) < 2:
        parser.error("At least 2 session databases required")

    output = run_compare(args.sessions, args.report)
    print(output)


if __name__ == "__main__":
    main()
