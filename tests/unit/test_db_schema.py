"""Test SQLite schema creation, WAL mode, and insert/query round-trips."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest
from nodalarc.db.queries import (
    get_metadata,
    insert_adapter_event,
    insert_config_change,
    insert_convergence_result,
    insert_latency_update,
    insert_link_down,
    insert_link_up,
    insert_probe_result,
    query_adapter_events,
    query_convergence_events,
    query_link_events,
    query_probe_results,
    set_metadata,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult

T0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
T1 = datetime(2025, 1, 1, 0, 1, 0, tzinfo=UTC)
T2 = datetime(2025, 1, 1, 0, 2, 0, tzinfo=UTC)
T3 = datetime(2025, 1, 1, 0, 3, 0, tzinfo=UTC)
WALL = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


def _link_up(sim_time=T0) -> LinkUp:
    return LinkUp(
        sim_time=sim_time,
        wall_time=WALL,
        node_a="sat-P00S00",
        node_b="sat-P00S01",
        link_type="isl",
        interface_a="isl0",
        interface_b="isl1",
        latency_ms=2.5,
        bandwidth_mbps=1000.0,
        range_km=749.481145,
        reason="visibility",
    )


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    create_tables(conn)
    yield conn
    conn.close()


class TestSchemaCreation:
    def test_all_six_tables_created(self, db):
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        expected = {
            "link_events",
            "convergence_events",
            "probe_results",
            "adapter_events",
            "session_metadata",
            "config_changes",
        }
        assert expected.issubset(table_names)

    def test_indexes_created(self, db):
        indexes = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {i[0] for i in indexes}
        assert "idx_link_events_sim_time" in index_names
        assert "idx_link_events_nodes" in index_names
        assert "idx_convergence_sim_time" in index_names
        assert "idx_probe_results_flow" in index_names
        assert "idx_adapter_events_node" in index_names

    def test_wal_mode_enabled(self, tmp_path):
        db_path = tmp_path / "wal_test.db"
        conn = sqlite3.connect(str(db_path))
        create_tables(conn)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_idempotent_creation(self, db):
        create_tables(db)
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert len(tables) >= 6


class TestLinkEventQueries:
    def test_insert_link_up(self, db):
        row_id = insert_link_up(db, _link_up())
        assert row_id is not None and row_id > 0

    def test_insert_link_down(self, db):
        event = LinkDown(
            sim_time=T0,
            wall_time=WALL,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            reason="los-blocked",
        )
        row_id = insert_link_down(db, event)
        assert row_id > 0

    def test_insert_latency_update(self, db):
        event = LatencyUpdate(
            sim_time=T0,
            wall_time=WALL,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            latency_ms=3.1,
            range_km=1200.0,
        )
        row_id = insert_latency_update(db, event)
        assert row_id > 0

    def test_query_link_events_by_time(self, db):
        for t in [T0, T1, T2]:
            insert_link_up(db, _link_up(sim_time=t))
        # T1 is between T0 and T2
        results = query_link_events(db, start_time=T1.isoformat(), end_time=T1.isoformat())
        assert len(results) == 1
        assert results[0]["sim_time"] == T1.isoformat()

    def test_query_link_events_by_node(self, db):
        insert_link_up(db, _link_up())
        insert_link_up(
            db,
            LinkUp(
                sim_time=T0,
                wall_time=WALL,
                node_a="sat-P01S00",
                node_b="sat-P01S01",
                link_type="isl",
                interface_a="isl0",
                interface_b="isl1",
                latency_ms=2.5,
                bandwidth_mbps=1000.0,
                range_km=749.481145,
                reason="vis",
            ),
        )
        results = query_link_events(db, node="sat-P00S00")
        assert len(results) == 1

    def test_query_returns_all_when_no_filter(self, db):
        for i in range(5):
            insert_link_up(db, _link_up())
        results = query_link_events(db)
        assert len(results) == 5


class TestConvergenceQueries:
    def test_insert_and_query(self, db):
        result = ConvergenceResult(
            event_id="evt-001",
            converged=True,
            duration_ms=1500.0,
            packets_lost=0,
            packets_sent=15,
            sim_time_start=T0,
            sim_time_end=T1,
            wall_time_start=WALL,
            wall_time_end=WALL,
        )
        row_id = insert_convergence_result(db, result)
        assert row_id > 0

        rows = query_convergence_events(db)
        assert len(rows) == 1
        assert rows[0]["converged"] == 1
        assert rows[0]["duration_ms"] == 1500.0
        assert rows[0]["wall_time_start"] == WALL.isoformat()
        assert rows[0]["wall_time_end"] == WALL.isoformat()

    def test_query_by_event_id(self, db):
        insert_convergence_result(
            db,
            ConvergenceResult(
                event_id="evt-001",
                converged=True,
                duration_ms=100.0,
                packets_lost=0,
                packets_sent=10,
                sim_time_start=T0,
                sim_time_end=T1,
                wall_time_start=WALL,
                wall_time_end=WALL,
            ),
        )
        insert_convergence_result(
            db,
            ConvergenceResult(
                event_id="evt-002",
                converged=False,
                duration_ms=30000.0,
                packets_lost=5,
                packets_sent=100,
                sim_time_start=T1,
                sim_time_end=T2,
                wall_time_start=WALL,
                wall_time_end=WALL,
            ),
        )
        rows = query_convergence_events(db, event_id="evt-002")
        assert len(rows) == 1
        assert rows[0]["converged"] == 0

    def test_event_id_unique_constraint(self, db):
        result = ConvergenceResult(
            event_id="evt-dup",
            converged=True,
            duration_ms=100.0,
            packets_lost=0,
            packets_sent=10,
            sim_time_start=T0,
            sim_time_end=T1,
            wall_time_start=WALL,
            wall_time_end=WALL,
        )
        insert_convergence_result(db, result)
        with pytest.raises(sqlite3.IntegrityError):
            insert_convergence_result(db, result)

    def test_triggering_link_event_id(self, db):
        link_id = insert_link_up(db, _link_up())
        result = ConvergenceResult(
            event_id="evt-linked",
            converged=True,
            duration_ms=200.0,
            packets_lost=0,
            packets_sent=5,
            sim_time_start=T0,
            sim_time_end=T1,
            wall_time_start=WALL,
            wall_time_end=WALL,
            triggering_link_event_id=link_id,
        )
        row_id = insert_convergence_result(db, result)
        assert row_id > 0
        rows = query_convergence_events(db, event_id="evt-linked")
        assert rows[0]["triggering_link_event_id"] == link_id


class TestProbeQueries:
    def test_insert_and_query(self, db):
        result = ProbeResult(
            sim_time=T0,
            wall_time=WALL,
            flow_id="flow-1",
            src_node="gs-hawthorne",
            dst_node="gs-ashburn",
            packets_sent=100,
            packets_received=99,
            latency_min_ms=20.0,
            latency_max_ms=30.0,
            latency_avg_ms=25.0,
            jitter_ms=2.0,
        )
        row_id = insert_probe_result(db, result)
        assert row_id > 0

        rows = query_probe_results(db, flow_id="flow-1")
        assert len(rows) == 1
        assert rows[0]["latency_avg_ms"] == 25.0


class TestAdapterQueries:
    def test_insert_and_query(self, db):
        event = AdapterEvent(
            sim_time=T0,
            wall_time=WALL,
            node_id="sat-P00S00",
            event_type="adjacency-up",
            event_data={"neighbor": "sat-P00S01", "interface": "isl0"},
        )
        row_id = insert_adapter_event(db, event)
        assert row_id > 0

        rows = query_adapter_events(db, node_id="sat-P00S00")
        assert len(rows) == 1
        assert rows[0]["event_data"] == {"neighbor": "sat-P00S01", "interface": "isl0"}


class TestMetadata:
    def test_set_and_get(self, db):
        set_metadata(db, "session_name", "iridium-small-36-isis-flat")
        assert get_metadata(db, "session_name") == "iridium-small-36-isis-flat"

    def test_get_missing_key(self, db):
        assert get_metadata(db, "nonexistent") is None

    def test_upsert_overwrites(self, db):
        set_metadata(db, "key", "value1")
        set_metadata(db, "key", "value2")
        assert get_metadata(db, "key") == "value2"


class TestConfigChanges:
    def test_insert(self, db):
        row_id = insert_config_change(
            db,
            sim_time="2025-01-01T00:00:00+00:00",
            wall_time="2025-06-01T12:00:00+00:00",
            change_type="flow_add",
            description="Added flow ashburn-to-frankfurt",
        )
        assert row_id > 0

    def test_insert_with_snapshot(self, db):
        row_id = insert_config_change(
            db,
            sim_time="2025-01-01T00:00:00+00:00",
            wall_time="2025-06-01T12:00:00+00:00",
            change_type="reconfig",
            description="Reconfigured IS-IS metrics",
            config_snapshot='{"isis": {"metric": 100}}',
        )
        assert row_id > 0


class TestConcurrentAccess:
    def test_concurrent_reads_while_writing(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        conn_write = sqlite3.connect(str(db_path))
        create_tables(conn_write)

        for i in range(10):
            insert_link_up(conn_write, _link_up())

        results = []
        errors = []

        def reader():
            try:
                conn_read = sqlite3.connect(str(db_path))
                rows = query_link_events(conn_read)
                results.append(len(rows))
                conn_read.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(r == 10 for r in results)
        conn_write.close()
