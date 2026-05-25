"""Integration tests for MI adapter event collection and persistence."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def session_db(tmp_path):
    """Path for session SQLite database."""
    return str(tmp_path / "session.db")


def test_collector_records_and_publishes_adapter_events(session_db, monkeypatch):
    """A passive adapter event must survive MI collection, SQLite, and publish."""
    from measurement import mi_main
    from nodalarc.db.queries import query_adapter_events
    from nodalarc.db.schema import create_tables
    from nodalarc.models.metrics import AdapterEvent

    event = AdapterEvent(
        sim_time=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        wall_time=datetime(2026, 5, 25, 12, 0, 1, tzinfo=UTC),
        node_id="sat-P00S00",
        event_type="adjacency_up",
        event_data={
            "source": "vtysh_poll",
            "system_id": "0000.0000.0001",
            "interface": "isl0",
            "state": "Up",
        },
    )

    class FakeAdapter:
        def __init__(self) -> None:
            self.poll_calls: list[str] = []
            self.get_events_calls: list[str] = []

        def poll(self, node_id: str) -> None:
            self.poll_calls.append(node_id)

        def get_events(self, node_id: str) -> list[AdapterEvent]:
            self.get_events_calls.append(node_id)
            return [event]

    conn = sqlite3.connect(session_db, check_same_thread=False)
    create_tables(conn)
    adapter = FakeAdapter()
    published: list[tuple[str, bytes]] = []

    service = object.__new__(mi_main.MIService)
    service._namespace = "nodalarc"
    service._adapter = adapter
    service._db_conn = conn
    service._db_lock = threading.Lock()
    service._subj_adapter = "nodalarc.mi.run-test.adapter"
    service._flow_manager = None
    service._publish_sync = lambda subject, payload: published.append((subject, payload))

    monkeypatch.setattr(
        mi_main,
        "_discover_pods",
        lambda _namespace: [
            {
                "node_id": "sat-P00S00",
                "pod_name": "sat-p00s00",
                "role": "satellite",
                "pod_ip": "10.42.0.10",
            }
        ],
    )
    service.collect_once()

    assert adapter.poll_calls == ["sat-P00S00"]
    assert adapter.get_events_calls == ["sat-P00S00"]

    results = query_adapter_events(conn, node_id="sat-P00S00")
    assert len(results) == 1
    assert results[0]["event_type"] == "adjacency_up"
    assert results[0]["event_data"] == event.event_data

    assert len(published) == 1
    subject, payload = published[0]
    assert subject == "nodalarc.mi.run-test.adapter"
    assert AdapterEvent.model_validate_json(payload) == event
    conn.close()
