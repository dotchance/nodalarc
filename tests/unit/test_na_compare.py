"""Test na-compare cross-session analysis tool.

Creates two temp SQLite databases with known data, runs each
report type, and verifies the output contains expected information.
"""

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nodalarc.db.schema import create_tables
from tools.na_compare import (
    _attach_databases,
    _count_table,
    _get_metadata,
    report_convergence,
    report_link_events,
    report_probe_results,
    report_summary,
    run_compare,
)


@pytest.fixture()
def two_session_dbs(tmp_path):
    """Create two temp SQLite databases with known test data."""
    db1_path = str(tmp_path / "session1.db")
    db2_path = str(tmp_path / "session2.db")

    for db_path, session_name, routing_stack in [
        (db1_path, "isis-run", "frr-isis-sr"),
        (db2_path, "ospf-run", "frr-ospf-te"),
    ]:
        conn = sqlite3.connect(db_path)
        create_tables(conn)

        # Session metadata
        conn.execute(
            "INSERT INTO session_metadata (key, value) VALUES (?, ?)",
            ("session_name", session_name),
        )
        conn.execute(
            "INSERT INTO session_metadata (key, value) VALUES (?, ?)",
            ("routing_stack", routing_stack),
        )

        # Link events
        for i, etype in enumerate(["LinkUp", "LinkDown", "LinkUp"]):
            conn.execute(
                """INSERT INTO link_events
                   (sim_time, wall_time, event_type, node_a, node_b, latency_ms, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"2026-01-01T00:00:{i:02d}",
                    f"2026-01-01T00:00:{i:02d}",
                    etype,
                    "sat-P00S00",
                    "sat-P00S01",
                    12.5 + i,
                    "test",
                ),
            )

        # Convergence events
        duration = 2500.0 if session_name == "isis-run" else 3200.0
        lost = 3 if session_name == "isis-run" else 5
        conn.execute(
            """INSERT INTO convergence_events
               (event_id, sim_time_start, sim_time_end, wall_time_start, wall_time_end,
                converged, duration_ms, packets_lost, packets_sent, triggering_link_event_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "conv-001",
                "2026-01-01T00:00:01",
                "2026-01-01T00:00:03",
                "2026-01-01T00:00:01",
                "2026-01-01T00:00:03",
                1,
                duration,
                lost,
                100,
                2,
            ),
        )

        # Probe results
        conn.execute(
            """INSERT INTO probe_results
               (sim_time, wall_time, flow_id, src_node, dst_node,
                packets_sent, packets_received, latency_min_ms, latency_max_ms,
                latency_avg_ms, jitter_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "2026-01-01T00:00:05",
                "2026-01-01T00:00:05",
                "test-flow",
                "gs-hawthorne",
                "gs-ashburn",
                100,
                95,
                20.0,
                45.0,
                30.0,
                5.0,
            ),
        )

        conn.commit()
        conn.close()

    return db1_path, db2_path


class TestAttachDatabases:
    def test_attach_two_databases(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        # Verify we can query both
        row = conn.execute("SELECT COUNT(*) AS cnt FROM s1.link_events").fetchone()
        assert row["cnt"] == 3
        row = conn.execute("SELECT COUNT(*) AS cnt FROM s2.link_events").fetchone()
        assert row["cnt"] == 3
        conn.close()


class TestGetMetadata:
    def test_reads_metadata(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        meta = _get_metadata(conn, "s1")
        assert meta["session_name"] == "isis-run"
        assert meta["routing_stack"] == "frr-isis-sr"
        conn.close()


class TestCountTable:
    def test_counts_rows(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        assert _count_table(conn, "s1", "link_events") == 3
        assert _count_table(conn, "s1", "convergence_events") == 1
        assert _count_table(conn, "s1", "probe_results") == 1
        conn.close()

    def test_nonexistent_table_returns_zero(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        assert _count_table(conn, "s1", "nonexistent_table") == 0
        conn.close()


class TestReportSummary:
    def test_contains_metadata(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_summary(conn, ["s1", "s2"])
        assert "SESSION COMPARISON" in output
        assert "isis-run" in output
        assert "ospf-run" in output
        assert "frr-isis-sr" in output
        assert "frr-ospf-te" in output
        conn.close()

    def test_contains_table_counts(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_summary(conn, ["s1", "s2"])
        assert "link_events" in output
        assert "convergence_events" in output
        assert "probe_results" in output
        conn.close()


class TestReportConvergence:
    def test_contains_convergence_data(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_convergence(conn, ["s1", "s2"])
        assert "CONVERGENCE" in output
        assert "conv-001" in output
        assert "2500" in output  # IS-IS duration
        assert "3200" in output  # OSPF duration
        conn.close()

    def test_side_by_side_comparison(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_convergence(conn, ["s1", "s2"])
        assert "Side-by-side" in output
        assert "+700" in output  # delta: 3200 - 2500
        conn.close()


class TestReportLinkEvents:
    def test_contains_link_data(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_link_events(conn, ["s1", "s2"])
        assert "LINK EVENTS" in output
        assert "sat-P00S00" in output
        assert "sat-P00S01" in output
        assert "LinkUp" in output
        assert "LinkDown" in output
        conn.close()

    def test_event_count_by_type(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_link_events(conn, ["s1", "s2"])
        assert "Event count by type" in output
        conn.close()


class TestReportProbeResults:
    def test_contains_probe_data(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_probe_results(conn, ["s1", "s2"])
        assert "PROBE RESULTS" in output
        assert "test-flow" in output
        assert "gs-hawthorne" not in output or "test-flow" in output  # flow_id shown
        conn.close()

    def test_shows_loss_percentage(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_probe_results(conn, ["s1", "s2"])
        assert "5.0%" in output  # 5 lost of 100 sent
        conn.close()


class TestRunCompare:
    def test_summary_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "summary")
        assert "SESSION COMPARISON" in output

    def test_convergence_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "convergence")
        assert "CONVERGENCE" in output

    def test_link_events_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "link-events")
        assert "LINK EVENTS" in output

    def test_probe_results_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "probe-results")
        assert "PROBE RESULTS" in output

    def test_nonexistent_db_exits(self):
        with pytest.raises(SystemExit):
            run_compare(["/nonexistent/db1.db", "/nonexistent/db2.db"], "summary")


class TestEmptyDatabases:
    """Verify reports handle empty databases gracefully."""

    def test_summary_on_empty_dbs(self, tmp_path):
        db1 = str(tmp_path / "empty1.db")
        db2 = str(tmp_path / "empty2.db")
        for db_path in [db1, db2]:
            conn = sqlite3.connect(db_path)
            create_tables(conn)
            conn.close()

        output = run_compare([db1, db2], "summary")
        assert "SESSION COMPARISON" in output

    def test_convergence_on_empty_dbs(self, tmp_path):
        db1 = str(tmp_path / "empty1.db")
        db2 = str(tmp_path / "empty2.db")
        for db_path in [db1, db2]:
            conn = sqlite3.connect(db_path)
            create_tables(conn)
            conn.close()

        output = run_compare([db1, db2], "convergence")
        assert "no events" in output
