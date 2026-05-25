"""Test na-compare cross-session analysis semantics.

Creates two temp SQLite databases with known data, runs each report type, and
parses the rendered output into metadata, counts, deltas, and probe aggregates.
"""

import sqlite3

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


def _parse_summary_table_counts(output: str) -> dict[str, tuple[int, int]]:
    counts = {}
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] in {
            "link_events",
            "convergence_events",
            "probe_results",
            "adapter_events",
            "snapshots",
        }:
            counts[parts[0]] = (int(parts[1]), int(parts[2]))
    return counts


def _parse_summary_metadata(output: str) -> dict[str, tuple[str, str]]:
    metadata = {}
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] in {"routing_stack", "session_name"}:
            metadata[parts[0]] = (parts[1], parts[2])
    return metadata


def _parse_convergence_rows(output: str) -> dict[str, dict[str, dict[str, float | int | str]]]:
    rows: dict[str, dict[str, dict[str, float | int | str]]] = {}
    current_alias = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in {"--- s1 ---", "--- s2 ---"}:
            current_alias = stripped.removeprefix("--- ").removesuffix(" ---")
            rows[current_alias] = {}
            continue
        parts = stripped.split()
        if current_alias and len(parts) == 5 and parts[0].startswith("conv-"):
            rows[current_alias][parts[0]] = {
                "converged": parts[1],
                "duration_ms": float(parts[2]),
                "packets_lost": int(parts[3]),
                "packets_sent": int(parts[4]),
            }
    return rows


def _parse_convergence_deltas(output: str) -> dict[str, dict[str, float | int]]:
    deltas = {}
    in_side_by_side = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("--- Side-by-side"):
            in_side_by_side = True
            continue
        parts = stripped.split()
        if in_side_by_side and len(parts) == 6 and parts[0].startswith("conv-"):
            deltas[parts[0]] = {
                "s1_ms": float(parts[1]),
                "s2_ms": float(parts[2]),
                "delta_ms": float(parts[3]),
                "s1_lost": int(parts[4]),
                "s2_lost": int(parts[5]),
            }
    return deltas


def _parse_link_event_counts(output: str) -> dict[str, tuple[int, int]]:
    counts = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in {"LinkUp", "LinkDown", "LatencyUpdate"}:
            counts[parts[0]] = (int(parts[1]), int(parts[2]))
    return counts


def _parse_probe_flows_by_alias(output: str) -> dict[str, dict[str, dict[str, float | int]]]:
    rows: dict[str, dict[str, dict[str, float | int]]] = {}
    current_alias = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in {"--- s1 ---", "--- s2 ---"}:
            current_alias = stripped.removeprefix("--- ").removesuffix(" ---")
            rows[current_alias] = {}
            continue
        parts = stripped.split()
        if current_alias and len(parts) == 7 and parts[0] != "flow_id":
            rows[current_alias][parts[0]] = {
                "samples": int(parts[1]),
                "sent": int(parts[2]),
                "received": int(parts[3]),
                "loss_pct": float(parts[4].rstrip("%")),
                "avg_latency_ms": float(parts[5]),
                "jitter_ms": float(parts[6]),
            }
    return rows


class TestReportSummary:
    def test_reports_metadata_side_by_side(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_summary(conn, ["s1", "s2"])
        conn.close()

        assert _parse_summary_metadata(output) == {
            "routing_stack": ("frr-isis-sr", "frr-ospf-te"),
            "session_name": ("isis-run", "ospf-run"),
        }

    def test_reports_exact_table_counts_side_by_side(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_summary(conn, ["s1", "s2"])
        conn.close()

        assert _parse_summary_table_counts(output) == {
            "link_events": (3, 3),
            "convergence_events": (1, 1),
            "probe_results": (1, 1),
            "adapter_events": (0, 0),
            "snapshots": (0, 0),
        }


class TestReportConvergence:
    def test_reports_per_session_convergence_rows(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_convergence(conn, ["s1", "s2"])
        conn.close()

        assert _parse_convergence_rows(output) == {
            "s1": {
                "conv-001": {
                    "converged": "yes",
                    "duration_ms": 2500.0,
                    "packets_lost": 3,
                    "packets_sent": 100,
                }
            },
            "s2": {
                "conv-001": {
                    "converged": "yes",
                    "duration_ms": 3200.0,
                    "packets_lost": 5,
                    "packets_sent": 100,
                }
            },
        }

    def test_reports_matched_event_deltas(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_convergence(conn, ["s1", "s2"])
        conn.close()

        assert _parse_convergence_deltas(output) == {
            "conv-001": {
                "s1_ms": 2500.0,
                "s2_ms": 3200.0,
                "delta_ms": 700.0,
                "s1_lost": 3,
                "s2_lost": 5,
            }
        }


class TestReportLinkEvents:
    def test_reports_event_count_by_type_per_session(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_link_events(conn, ["s1", "s2"])
        conn.close()

        assert _parse_link_event_counts(output) == {
            "LinkUp": (2, 2),
            "LinkDown": (1, 1),
            "LatencyUpdate": (0, 0),
        }


class TestReportProbeResults:
    def test_reports_probe_delivery_and_latency_by_session(self, two_session_dbs):
        db1, db2 = two_session_dbs
        conn = _attach_databases([db1, db2])
        output = report_probe_results(conn, ["s1", "s2"])
        conn.close()

        expected = {
            "test-flow": {
                "samples": 1,
                "sent": 100,
                "received": 95,
                "loss_pct": 5.0,
                "avg_latency_ms": 30.0,
                "jitter_ms": 5.0,
            }
        }
        assert _parse_probe_flows_by_alias(output) == {"s1": expected, "s2": expected}


class TestRunCompare:
    def test_summary_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "summary")
        assert _parse_summary_table_counts(output)["link_events"] == (3, 3)

    def test_convergence_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "convergence")
        assert _parse_convergence_deltas(output)["conv-001"]["delta_ms"] == 700.0

    def test_link_events_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "link-events")
        assert _parse_link_event_counts(output)["LinkUp"] == (2, 2)

    def test_probe_results_report(self, two_session_dbs):
        db1, db2 = two_session_dbs
        output = run_compare([db1, db2], "probe-results")
        assert _parse_probe_flows_by_alias(output)["s1"]["test-flow"]["loss_pct"] == 5.0

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
        assert _parse_summary_table_counts(output)["link_events"] == (0, 0)

    def test_convergence_on_empty_dbs(self, tmp_path):
        db1 = str(tmp_path / "empty1.db")
        db2 = str(tmp_path / "empty2.db")
        for db_path in [db1, db2]:
            conn = sqlite3.connect(db_path)
            create_tables(conn)
            conn.close()

        output = run_compare([db1, db2], "convergence")
        assert _parse_convergence_rows(output) == {"s1": {}, "s2": {}}
        assert "no events" in output
