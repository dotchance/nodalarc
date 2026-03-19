"""Tests for tools/na_report.py — single-session report tool.

Pattern follows test_na_compare.py: create temp SQLite DB with known data,
run each report type, verify expected strings in output.
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
            interface_a="isl0",
            interface_b="isl0",
            latency_ms=3.5,
            bandwidth_mbps=1000.0,
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
            interface_a="isl1",
            interface_b="isl1",
            latency_ms=12.0,
            bandwidth_mbps=1000.0,
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


class TestReportSummary:
    def test_contains_metadata(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_summary(conn)
        conn.close()
        assert "isis-test-run" in output
        assert "custom-example" in output
        assert "frr-isis" in output

    def test_contains_table_counts(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_summary(conn)
        conn.close()
        assert "link_events" in output
        assert "convergence_events" in output
        assert "probe_results" in output

    def test_empty_db(self, empty_db: str):
        conn = sqlite3.connect(empty_db)
        output = report_summary(conn)
        conn.close()
        assert "SUMMARY" in output
        assert "(no metadata)" in output


class TestReportConvergence:
    def test_contains_events(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_convergence(conn)
        conn.close()
        assert "conv-001" in output
        assert "conv-002" in output
        assert "120.5" in output
        assert "85.0" in output

    def test_contains_statistics(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_convergence(conn)
        conn.close()
        assert "Mean duration" in output
        assert "Median duration" in output
        assert "Max duration" in output
        assert "Total pkt loss" in output

    def test_empty_db(self, empty_db: str):
        conn = sqlite3.connect(empty_db)
        output = report_convergence(conn)
        conn.close()
        assert "(no convergence events)" in output


class TestReportLinkEvents:
    def test_contains_events(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_link_events(conn)
        conn.close()
        assert "sat-P00S00" in output
        assert "sat-P00S01" in output
        assert "LinkUp" in output
        assert "LinkDown" in output

    def test_contains_type_counts(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_link_events(conn)
        conn.close()
        assert "Event counts by type" in output

    def test_contains_node_churn(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_link_events(conn)
        conn.close()
        assert "Per-node adjacency churn" in output

    def test_empty_db(self, empty_db: str):
        conn = sqlite3.connect(empty_db)
        output = report_link_events(conn)
        conn.close()
        assert "(no link events)" in output


class TestReportProbeResults:
    def test_contains_flow_stats(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_probe_results(conn)
        conn.close()
        assert "flow-alpha" in output
        assert "flow-beta" in output

    def test_loss_percentage(self, session_db: str):
        conn = sqlite3.connect(session_db)
        output = report_probe_results(conn)
        conn.close()
        # flow-alpha: 100 sent, 98 received = 2% loss
        assert "2.0%" in output

    def test_empty_db(self, empty_db: str):
        conn = sqlite3.connect(empty_db)
        output = report_probe_results(conn)
        conn.close()
        assert "(no probe results)" in output


class TestRunReport:
    def test_summary_via_run_report(self, session_db: str):
        output = run_report(session_db, "summary")
        assert "SUMMARY" in output

    def test_convergence_via_run_report(self, session_db: str):
        output = run_report(session_db, "convergence")
        assert "CONVERGENCE" in output

    def test_link_events_via_run_report(self, session_db: str):
        output = run_report(session_db, "link-events")
        assert "LINK EVENTS" in output

    def test_probe_results_via_run_report(self, session_db: str):
        output = run_report(session_db, "probe-results")
        assert "PROBE RESULTS" in output

    def test_nonexistent_db(self, tmp_path: Path):
        fake_path = str(tmp_path / "nonexistent.db")
        with pytest.raises(SystemExit):
            run_report(fake_path, "summary")
