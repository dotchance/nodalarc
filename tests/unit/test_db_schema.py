"""Test SQLite schema creation, WAL mode, and insert/query round-trips."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest
from nodalarc.db.queries import (
    get_metadata,
    insert_adapter_event,
    insert_convergence_result,
    insert_latency_update,
    insert_link_down,
    insert_link_up,
    insert_ome_lifecycle_event,
    insert_operator_intervention_event,
    insert_probe_result,
    query_adapter_events,
    query_convergence_events,
    query_link_events,
    query_ome_lifecycle_events,
    query_probe_results,
    recorded_session_id,
    set_metadata,
)
from nodalarc.db.schema import (
    SCHEMA_VERSION,
    HistorySchemaError,
    create_tables,
    require_schema_version,
)
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
            "ome_lifecycle_events",
            "operator_interventions",
        }
        assert expected.issubset(table_names)

    def test_indexes_created(self, db):
        indexes = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {i[0] for i in indexes}
        assert "idx_link_events_time" in index_names
        assert "idx_link_events_nodes" in index_names
        assert "idx_convergence_time" in index_names
        assert "idx_probe_results_flow" in index_names
        assert "idx_adapter_events_node" in index_names
        assert "idx_ome_lifecycle_session" in index_names
        assert "idx_ome_lifecycle_pair" in index_names

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


class TestSchemaVersion:
    def test_a_new_database_records_the_schema_version(self, db):
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        require_schema_version(db)

    def test_a_database_from_another_release_is_refused_never_migrated(self, tmp_path):
        """Tables without a version (the earlier shared schema) are not altered."""
        path = tmp_path / "earlier.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE link_events (id INTEGER PRIMARY KEY, sim_time TEXT)")
        conn.commit()

        with pytest.raises(HistorySchemaError, match="schema version 0 is not"):
            create_tables(conn)
        with pytest.raises(HistorySchemaError, match="schema version 0 is not"):
            require_schema_version(conn)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(link_events)")]
        assert columns == ["id", "sim_time"]
        conn.close()


class TestRecordedSession:
    def test_a_history_file_names_its_one_session(self, db):
        set_metadata(db, session_id="run-a", key="session_name", value="earth-geo-tdrs")
        assert recorded_session_id(db) == "run-a"

    @pytest.mark.parametrize("sessions", [(), ("run-a", "run-b")], ids=["none", "two"])
    def test_a_file_without_exactly_one_session_is_refused(self, db, sessions):
        for session in sessions:
            set_metadata(db, session_id=session, key="session_name", value="x")
        with pytest.raises(ValueError, match=f"records {len(sessions)} sessions"):
            recorded_session_id(db)


class TestLinkEventQueries:
    def test_insert_link_up(self, db):
        row_id = insert_link_up(db, _link_up(), session_id="run-test")
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
        row_id = insert_link_down(db, event, session_id="run-test")
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
        row_id = insert_latency_update(db, event, session_id="run-test")
        assert row_id > 0

    def test_query_link_events_by_time(self, db):
        for t in [T0, T1, T2]:
            insert_link_up(db, _link_up(sim_time=t), session_id="run-test")
        # T1 is between T0 and T2
        results = query_link_events(
            db, start_time=T1.isoformat(), end_time=T1.isoformat(), session_id="run-test"
        )
        assert len(results) == 1
        assert results[0]["sim_time"] == T1.isoformat()

    def test_query_link_events_by_node(self, db):
        insert_link_up(db, _link_up(), session_id="run-test")
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
                range_km=749.481145,
                reason="vis",
            ),
            session_id="run-test",
        )
        results = query_link_events(db, node="sat-P00S00", session_id="run-test")
        assert len(results) == 1

    def test_query_returns_all_when_no_filter(self, db):
        for i in range(5):
            insert_link_up(db, _link_up(), session_id="run-test")
        results = query_link_events(db, session_id="run-test")
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
        row_id = insert_convergence_result(db, result, session_id="run-test")
        assert row_id > 0

        rows = query_convergence_events(db, session_id="run-test")
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
            session_id="run-test",
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
            session_id="run-test",
        )
        rows = query_convergence_events(db, event_id="evt-002", session_id="run-test")
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
        insert_convergence_result(db, result, session_id="run-test")
        with pytest.raises(sqlite3.IntegrityError):
            insert_convergence_result(db, result, session_id="run-test")

    def test_triggering_link_event_id(self, db):
        link_id = insert_link_up(db, _link_up(), session_id="run-test")
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
        row_id = insert_convergence_result(db, result, session_id="run-test")
        assert row_id > 0
        rows = query_convergence_events(db, event_id="evt-linked", session_id="run-test")
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
        row_id = insert_probe_result(db, result, session_id="run-test")
        assert row_id > 0

        rows = query_probe_results(db, flow_id="flow-1", session_id="run-test")
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
        row_id = insert_adapter_event(db, event, session_id="run-test")
        assert row_id > 0

        rows = query_adapter_events(db, node_id="sat-P00S00", session_id="run-test")
        assert len(rows) == 1
        assert rows[0]["event_data"] == {"neighbor": "sat-P00S01", "interface": "isl0"}


class TestMetadata:
    def test_set_and_get(self, db):
        set_metadata(
            db, key="session_name", value="iridium-small-36-isis-flat", session_id="run-test"
        )
        assert (
            get_metadata(db, key="session_name", session_id="run-test")
            == "iridium-small-36-isis-flat"
        )

    def test_get_missing_key(self, db):
        assert get_metadata(db, key="nonexistent", session_id="run-test") is None

    def test_upsert_overwrites(self, db):
        set_metadata(db, key="key", value="value1", session_id="run-test")
        set_metadata(db, key="key", value="value2", session_id="run-test")
        assert get_metadata(db, key="key", session_id="run-test") == "value2"


class TestConcurrentAccess:
    def test_concurrent_reads_while_writing(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        conn_write = sqlite3.connect(str(db_path))
        create_tables(conn_write)

        for i in range(10):
            insert_link_up(conn_write, _link_up(), session_id="run-test")

        results = []
        errors = []

        def reader():
            try:
                conn_read = sqlite3.connect(str(db_path))
                rows = query_link_events(conn_read, session_id="run-test")
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


class TestOmeLifecyclePersistence:
    def test_lifecycle_terminal_ops_event_is_append_only_session_record(self, db):
        event = {
            "timestamp": WALL.isoformat(),
            "session_id": "session-a",
            "source": "ome",
            "hostname": "ome-0",
            "level": "info",
            "code": "MBB_TEARDOWN_TERMINAL",
            "message": "MBB teardown completed",
            "details": {
                "session_id": "session-a",
                "epoch_id": 7,
                "snapshot_seq": 42,
                "allocator_step": 123,
                "master_sim_time": T1.isoformat(),
                "gs_id": "gs-den",
                "teardown_id": "gs-den:sat-old->gs-den:sat-new",
                "old_pair": ["gs-den", "sat-old"],
                "successor_pair": ["gs-den", "sat-new"],
                "terminal_outcome": "teardown_completed",
                "source_allocation_event_category": "teardown_completed",
                "authority_before": {},
                "authority_after": {},
            },
        }
        second = {
            **event,
            "timestamp": T2.isoformat(),
            "details": {
                **event["details"],
                "allocator_step": 124,
                "terminal_outcome": "successor_aborted",
            },
        }

        first_id = insert_ome_lifecycle_event(db, event, session_id="session-a")
        second_id = insert_ome_lifecycle_event(db, second, session_id="session-a")

        rows = query_ome_lifecycle_events(db, session_id="session-a")
        assert first_id != second_id
        assert [row["terminal_outcome"] for row in rows] == [
            "teardown_completed",
            "successor_aborted",
        ]
        assert rows[0]["epoch_id"] == 7
        assert rows[0]["snapshot_seq"] == 42
        assert rows[0]["old_pair"] == '["gs-den", "sat-old"]'

    def test_non_ome_ops_event_is_refused(self, db):
        with pytest.raises(ValueError, match="must be an OME MBB_TEARDOWN_TERMINAL event"):
            insert_ome_lifecycle_event(
                db,
                {
                    "timestamp": WALL.isoformat(),
                    "session_id": "session-a",
                    "source": "scheduler",
                    "code": "MBB_TEARDOWN_TERMINAL",
                    "details": {"terminal_outcome": "teardown_completed"},
                },
                session_id="session-a",
            )
        assert query_ome_lifecycle_events(db, session_id="session-a") == []

    def test_lifecycle_event_missing_a_field_is_refused(self, db):
        event = {
            "timestamp": WALL.isoformat(),
            "session_id": "session-a",
            "source": "ome",
            "code": "MBB_TEARDOWN_TERMINAL",
            "details": {"epoch_id": 7, "terminal_outcome": "teardown_completed"},
        }
        with pytest.raises(ValueError, match="missing required field 'allocator_step'"):
            insert_ome_lifecycle_event(db, event, session_id="session-a")
        assert query_ome_lifecycle_events(db, session_id="session-a") == []


class TestOperatorInterventionPersistence:
    def test_intervention_events_are_append_only_and_mark_session_intervened(self, db):
        base_event = {
            "timestamp": T0.isoformat(),
            "session_id": "session-a",
            "hostname": "sched-host",
            "code": "OPERATOR_REPAIR_REQUESTED",
            "details": {
                "intervention_id": "repair-1",
                "wiring_generation": "sha256:" + "a" * 64,
                "scheduler_instance_id": "sched-1",
                "gs_id": "gs-den",
                "reason": "operator requested repair",
            },
        }
        second_event = {
            **base_event,
            "timestamp": T1.isoformat(),
            "code": "OPERATOR_REPAIR_SUCCEEDED",
            "details": {
                **base_event["details"],
                "reason": "operator repair matched current authority",
            },
        }

        first_id = insert_operator_intervention_event(db, base_event, session_id="session-a")
        second_id = insert_operator_intervention_event(db, second_event, session_id="session-a")

        rows = db.execute(
            "SELECT event_code FROM operator_interventions WHERE intervention_id = ? ORDER BY id",
            ("repair-1",),
        ).fetchall()
        intervened = get_metadata(db, key="operator_intervened", session_id="session-a")
        assert first_id != second_id
        assert [row[0] for row in rows] == [
            "OPERATOR_REPAIR_REQUESTED",
            "OPERATOR_REPAIR_SUCCEEDED",
        ]
        assert intervened == "true"

    def test_intervention_event_from_another_session_is_refused(self, db):
        event = {
            "timestamp": T0.isoformat(),
            "session_id": "session-b",
            "hostname": "sched-host",
            "code": "OPERATOR_REPAIR_REQUESTED",
            "details": {
                "intervention_id": "repair-1",
                "wiring_generation": "sha256:" + "a" * 64,
                "scheduler_instance_id": "sched-1",
                "gs_id": "gs-den",
            },
        }
        with pytest.raises(ValueError, match="belongs to session 'session-b', not 'session-a'"):
            insert_operator_intervention_event(db, event, session_id="session-a")
        assert db.execute("SELECT COUNT(*) FROM operator_interventions").fetchone()[0] == 0
