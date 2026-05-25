"""Tests for tools/na_report.py single-session report semantics.

The tests create a SQLite session with known events, run the user-facing report
functions, and parse the rendered tables back into counts and aggregates.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nodalarc.db.queries import (
    insert_convergence_result,
    insert_link_down,
    insert_link_up,
    insert_probe_result,
    set_metadata,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.models.metrics import ConvergenceResult, ProbeResult

from tools.na_report import (
    report_convergence,
    report_link_events,
    report_probe_results,
    report_summary,
    run_report,
)


@pytest.fixture()
def session_db(tmp_path: Path) -> str:
    """Create a temp SQLite DB with known test data."""
    db_path = str(tmp_path / "test_session.db")
    conn = sqlite3.connect(db_path)
    create_tables(conn)

    # Metadata
    set_metadata(conn, "session_name", "isis-test-run")
    set_metadata(conn, "constellation", "custom-example")
    set_metadata(conn, "routing_stack", "frr-isis")

    now = datetime.now(UTC)

    # Link events
    insert_link_up(
        conn,
        LinkUp(
            sim_time=now,
            wall_time=now,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl0",
            latency_ms=3.5,
            bandwidth_mbps=1000.0,
            range_km=1049.273603,
            reason="vis_gained",
        ),
    )
    insert_link_up(
        conn,
        LinkUp(
            sim_time=now,
            wall_time=now,
            node_a="sat-P00S00",
            node_b="sat-P01S00",
            link_type="isl",
            interface_a="isl1",
            interface_b="isl1",
            latency_ms=12.0,
            bandwidth_mbps=1000.0,
            range_km=3597.509496,
            reason="vis_gained",
        ),
    )
    insert_link_down(
        conn,
        LinkDown(
            sim_time=now,
            wall_time=now,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl0",
            reason="vis_lost",
        ),
    )

    # Convergence events
    insert_convergence_result(
        conn,
        ConvergenceResult(
            event_id="conv-001",
            sim_time_start=now,
            sim_time_end=now,
            wall_time_start=now,
            wall_time_end=now,
            converged=True,
            duration_ms=120.5,
            packets_lost=2,
            packets_sent=100,
            triggering_link_event_id=1,
        ),
    )
    insert_convergence_result(
        conn,
        ConvergenceResult(
            event_id="conv-002",
            sim_time_start=now,
            sim_time_end=now,
            wall_time_start=now,
            wall_time_end=now,
            converged=True,
            duration_ms=85.0,
            packets_lost=0,
            packets_sent=100,
            triggering_link_event_id=3,
        ),
    )

    # Probe results
    insert_probe_result(
        conn,
        ProbeResult(
            sim_time=now,
            wall_time=now,
            flow_id="flow-alpha",
            src_node="sat-P00S00",
            dst_node="sat-P01S01",
            packets_sent=50,
            packets_received=48,
            latency_min_ms=10.0,
            latency_max_ms=25.0,
            latency_avg_ms=15.0,
            jitter_ms=2.5,
        ),
    )
    insert_probe_result(
        conn,
        ProbeResult(
            sim_time=now,
            wall_time=now,
            flow_id="flow-alpha",
            src_node="sat-P00S00",
            dst_node="sat-P01S01",
            packets_sent=50,
            packets_received=50,
            latency_min_ms=9.0,
            latency_max_ms=22.0,
            latency_avg_ms=14.0,
            jitter_ms=2.0,
        ),
    )
    insert_probe_result(
        conn,
        ProbeResult(
            sim_time=now,
            wall_time=now,
            flow_id="flow-beta",
            src_node="sat-P00S01",
            dst_node="sat-P01S00",
            packets_sent=100,
            packets_received=95,
            latency_min_ms=20.0,
            latency_max_ms=40.0,
            latency_avg_ms=30.0,
            jitter_ms=5.0,
        ),
    )

    conn.close()
    return db_path


@pytest.fixture()
def empty_db(tmp_path: Path) -> str:
    """Create a temp SQLite DB with tables but no data."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    create_tables(conn)
    conn.close()
    return db_path


TABLE_NAMES = {
    "link_events",
    "convergence_events",
    "probe_results",
    "adapter_events",
    "session_metadata",
    "config_changes",
    "snapshots",
}


def _run_db_report(db_path: str, report_fn) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return report_fn(conn)
    finally:
        conn.close()


def _parse_metadata(output: str) -> dict[str, str]:
    metadata = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0] in {"session_name", "constellation", "routing_stack"}:
            metadata[parts[0]] = parts[1]
    return metadata


def _parse_table_counts(output: str) -> dict[str, int]:
    counts = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in TABLE_NAMES:
            counts[parts[0]] = int(parts[1])
    return counts


def _parse_convergence_events(output: str) -> dict[str, dict[str, float | int | str]]:
    rows = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 5 and parts[0].startswith("conv-"):
            rows[parts[0]] = {
                "converged": parts[1],
                "duration_ms": float(parts[2]),
                "packets_lost": int(parts[3]),
                "packets_sent": int(parts[4]),
            }
    return rows


def _parse_convergence_stats(output: str) -> dict[str, float | int]:
    stats = {}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Events:"):
            stats["events"] = int(stripped.split()[1])
        elif stripped.startswith("Mean duration:"):
            stats["mean_duration_ms"] = float(stripped.split()[2])
        elif stripped.startswith("Median duration:"):
            stats["median_duration_ms"] = float(stripped.split()[2])
        elif stripped.startswith("Max duration:"):
            stats["max_duration_ms"] = float(stripped.split()[2])
        elif stripped.startswith("Total pkt loss:"):
            stats["total_packet_loss"] = int(stripped.split()[3])
    return stats


def _parse_link_type_counts(output: str) -> dict[str, int]:
    counts = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in {"LinkUp", "LinkDown", "LatencyUpdate"}:
            counts[parts[0]] = int(parts[1])
    return counts


def _parse_node_churn(output: str) -> dict[str, int]:
    churn = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith("sat-"):
            churn[parts[0]] = int(parts[1])
    return churn


def _parse_probe_flows(output: str) -> dict[str, dict[str, float | int]]:
    flows = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 9 and parts[0].startswith("flow-"):
            flows[parts[0]] = {
                "samples": int(parts[1]),
                "sent": int(parts[2]),
                "received": int(parts[3]),
                "loss_pct": float(parts[4].rstrip("%")),
                "latency_min_ms": float(parts[5]),
                "latency_avg_ms": float(parts[6]),
                "latency_max_ms": float(parts[7]),
                "jitter_ms": float(parts[8]),
            }
    return flows


class TestReportSummary:
    def test_reports_metadata_and_exact_table_counts(self, session_db: str):
        output = _run_db_report(session_db, report_summary)

        assert _parse_metadata(output) == {
            "session_name": "isis-test-run",
            "constellation": "custom-example",
            "routing_stack": "frr-isis",
        }
        counts = _parse_table_counts(output)
        assert counts["link_events"] == 3
        assert counts["convergence_events"] == 2
        assert counts["probe_results"] == 3
        assert counts["adapter_events"] == 0
        assert counts["session_metadata"] == 3

    def test_empty_db(self, empty_db: str):
        output = _run_db_report(empty_db, report_summary)

        assert _parse_metadata(output) == {}
        assert _parse_table_counts(output)["link_events"] == 0
        assert "(no metadata)" in output


class TestReportConvergence:
    def test_reports_event_rows_and_statistics(self, session_db: str):
        output = _run_db_report(session_db, report_convergence)

        assert _parse_convergence_events(output) == {
            "conv-001": {
                "converged": "yes",
                "duration_ms": 120.5,
                "packets_lost": 2,
                "packets_sent": 100,
            },
            "conv-002": {
                "converged": "yes",
                "duration_ms": 85.0,
                "packets_lost": 0,
                "packets_sent": 100,
            },
        }
        assert _parse_convergence_stats(output) == {
            "events": 2,
            "mean_duration_ms": 102.8,
            "median_duration_ms": 102.8,
            "max_duration_ms": 120.5,
            "total_packet_loss": 2,
        }

    def test_empty_db(self, empty_db: str):
        output = _run_db_report(empty_db, report_convergence)

        assert _parse_convergence_events(output) == {}
        assert "(no convergence events)" in output


class TestReportLinkEvents:
    def test_reports_type_counts_and_node_churn(self, session_db: str):
        output = _run_db_report(session_db, report_link_events)

        assert _parse_link_type_counts(output) == {
            "LinkUp": 2,
            "LinkDown": 1,
            "LatencyUpdate": 0,
        }
        assert _parse_node_churn(output) == {
            "sat-P00S00": 3,
            "sat-P00S01": 2,
            "sat-P01S00": 1,
        }

    def test_empty_db(self, empty_db: str):
        output = _run_db_report(empty_db, report_link_events)

        assert _parse_link_type_counts(output) == {}
        assert _parse_node_churn(output) == {}
        assert "(no link events)" in output


class TestReportProbeResults:
    def test_reports_per_flow_delivery_and_latency_aggregates(self, session_db: str):
        output = _run_db_report(session_db, report_probe_results)

        assert _parse_probe_flows(output) == {
            "flow-alpha": {
                "samples": 2,
                "sent": 100,
                "received": 98,
                "loss_pct": 2.0,
                "latency_min_ms": 9.0,
                "latency_avg_ms": 14.5,
                "latency_max_ms": 25.0,
                "jitter_ms": 2.2,
            },
            "flow-beta": {
                "samples": 1,
                "sent": 100,
                "received": 95,
                "loss_pct": 5.0,
                "latency_min_ms": 20.0,
                "latency_avg_ms": 30.0,
                "latency_max_ms": 40.0,
                "jitter_ms": 5.0,
            },
        }

    def test_empty_db(self, empty_db: str):
        output = _run_db_report(empty_db, report_probe_results)

        assert _parse_probe_flows(output) == {}
        assert "(no probe results)" in output


class TestRunReport:
    def test_summary_via_run_report(self, session_db: str):
        output = run_report(session_db, "summary")
        assert _parse_table_counts(output)["link_events"] == 3

    def test_convergence_via_run_report(self, session_db: str):
        output = run_report(session_db, "convergence")
        assert _parse_convergence_stats(output)["events"] == 2

    def test_link_events_via_run_report(self, session_db: str):
        output = run_report(session_db, "link-events")
        assert _parse_link_type_counts(output)["LinkUp"] == 2

    def test_probe_results_via_run_report(self, session_db: str):
        output = run_report(session_db, "probe-results")
        assert _parse_probe_flows(output)["flow-alpha"]["loss_pct"] == 2.0

    def test_nonexistent_db(self, tmp_path: Path):
        fake_path = str(tmp_path / "nonexistent.db")
        with pytest.raises(SystemExit):
            run_report(fake_path, "summary")
