"""Integration test: DE dispatcher processes timeline correctly.

PRD Appendix B: loads a pre-computed timeline for the custom-example
constellation, runs the discrete-event dispatcher through all events,
verifies that the convergence gate is called for each link state change,
and checks that the SQLite database contains the expected records.

Does NOT require K3s — runs against a pre-computed timeline file.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import zmq

from nodalarc.db.queries import query_convergence_events, query_link_events
from nodalarc.db.schema import create_tables
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.zmq_channels import (
    mi_convergence_gate_bind,
    to_events_connect,
    decode_message,
    TOPIC_LINK_UP,
    TOPIC_LINK_DOWN,
)

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def four_node_session_path():
    """Create temp session config for custom-example constellation."""
    import tempfile
    import yaml

    session = {
        "session": {"name": "custom-example-de-test"},
        "constellation": "configs/constellations/custom-example.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "stack": "configs/routing-stacks/frr-isis-sr",
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"mode": "discrete-event", "step_seconds": 10},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", dir=str(PROJECT_ROOT), delete=False,
    ) as f:
        yaml.dump(session, f)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def timeline_path(four_node_session_path, tmp_path):
    """Generate timeline for custom-example constellation."""
    from ome.main import run as ome_run
    return ome_run(four_node_session_path, str(tmp_path))


@pytest.fixture
def db_conn(tmp_path):
    """Create an in-memory SQLite database with schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    create_tables(conn)
    return conn


class TestDiscreteEventProcessing:
    def test_timeline_produces_events(self, timeline_path):
        """Pre-computed timeline contains expected event types."""
        events = []
        with open(timeline_path) as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        assert len(events) > 0

        types = {e["event_type"] for e in events}
        assert "ClockTick" in types
        assert "Snapshot" in types

    def test_dispatcher_processes_full_timeline(self, timeline_path, db_conn):
        """DE dispatcher processes all events and records to SQLite."""
        from orchestrator.discrete_event_dispatcher import DiscreteEventDispatcher

        # Build a minimal interface map for the 4-node constellation
        # custom-example: 2 planes × 2 sats, ISLs: intra-plane + cross-plane
        from nodalarc.models.addressing import (
            AddressingScheme,
            assign_isl_neighbors,
            neighbors_by_node,
        )
        from nodalarc.models.constellation import ConstellationConfig
        from ome.constellation_loader import resolve_constellation_terminals
        from pydantic import TypeAdapter
        import yaml

        constellation_data = yaml.safe_load(
            (PROJECT_ROOT / "configs/constellations/custom-example.yaml").read_text(),
        )
        adapter = TypeAdapter(ConstellationConfig)
        constellation = adapter.validate_python(constellation_data)
        resolve_constellation_terminals(constellation)
        addressing = AddressingScheme()

        neighbors = assign_isl_neighbors(constellation, addressing)
        by_node = neighbors_by_node(neighbors)

        interface_map: dict[tuple[str, str], tuple[str, str]] = {}
        bandwidth_map: dict[tuple[str, str], float] = {}
        for node_id, assignments in by_node.items():
            for na in assignments:
                pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
                if pair not in interface_map:
                    interface_map[pair] = (na.interface, "")
                    bandwidth_map[pair] = 1000.0
                else:
                    existing = interface_map[pair]
                    if existing[0] and not existing[1]:
                        interface_map[pair] = (existing[0], na.interface)

        # Add GS-satellite pairs
        from ome.constellation_loader import expand_constellation, load_ground_stations
        gs_file = load_ground_stations(
            PROJECT_ROOT / "configs/ground-stations/sets/us-conus.yaml",
        )
        satellites = expand_constellation(constellation)
        for station in gs_file.stations:
            gs_id = addressing.gs_id(station.name)
            for sat in satellites:
                sat_id = addressing.sat_id(sat.plane, sat.slot)
                pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                interface_map[pair] = ("gnd0", "gnd0")
                bandwidth_map[pair] = 1000.0

        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        dispatcher = DiscreteEventDispatcher(
            timeline_path=Path(timeline_path),
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            db_conn=db_conn,
            use_convergence_gate=False,  # No convergence gate for this test
            dwell_s=0.0,  # No dwell — process as fast as possible
            max_idle_timeouts=1,  # Exit after processing finite file
        )
        dispatcher.run()

        # Verify SQLite has link events recorded
        link_events = query_link_events(db_conn)
        assert len(link_events) > 0

        # Should have both LinkUp and LinkDown events
        event_types = {e["event_type"] for e in link_events}
        assert "LinkUp" in event_types
        # LinkDown may or may not be present depending on timeline duration

    def test_dispatcher_with_convergence_stub(self, timeline_path, db_conn):
        """DE dispatcher calls convergence gate and records results."""
        from orchestrator.discrete_event_dispatcher import DiscreteEventDispatcher

        # Start convergence stub in subprocess
        stub_proc = subprocess.Popen(
            [sys.executable, "-m", "measurement.stubs.convergence_stub"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)  # Let stub bind

        try:
            # Build minimal maps (same as above)
            from nodalarc.models.addressing import (
                AddressingScheme,
                assign_isl_neighbors,
                neighbors_by_node,
            )
            from nodalarc.models.constellation import ConstellationConfig
            from ome.constellation_loader import expand_constellation, resolve_constellation_terminals
            from pydantic import TypeAdapter
            import yaml

            constellation_data = yaml.safe_load(
                (PROJECT_ROOT / "configs/constellations/custom-example.yaml").read_text(),
            )
            adapter = TypeAdapter(ConstellationConfig)
            constellation = adapter.validate_python(constellation_data)
            resolve_constellation_terminals(constellation)
            addressing = AddressingScheme()

            neighbors = assign_isl_neighbors(constellation, addressing)
            by_node = neighbors_by_node(neighbors)

            interface_map: dict[tuple[str, str], tuple[str, str]] = {}
            bandwidth_map: dict[tuple[str, str], float] = {}
            for node_id, assignments in by_node.items():
                for na in assignments:
                    pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
                    if pair not in interface_map:
                        interface_map[pair] = (na.interface, "")
                        bandwidth_map[pair] = 1000.0
                    else:
                        existing = interface_map[pair]
                        if existing[0] and not existing[1]:
                            interface_map[pair] = (existing[0], na.interface)

            from ome.constellation_loader import load_ground_stations as load_gs
            gs_file = load_gs(
                PROJECT_ROOT / "configs/ground-stations/sets/us-conus.yaml",
            )
            satellites = expand_constellation(constellation)
            for station in gs_file.stations:
                gs_id = addressing.gs_id(station.name)
                for sat in satellites:
                    sat_id = addressing.sat_id(sat.plane, sat.slot)
                    pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                    interface_map[pair] = ("gnd0", "gnd0")
                    bandwidth_map[pair] = 1000.0

            override_set: set[tuple[str, str]] = set()
            lock = threading.Lock()

            dispatcher = DiscreteEventDispatcher(
                timeline_path=Path(timeline_path),
                interface_map=interface_map,
                bandwidth_map=bandwidth_map,
                override_set=override_set,
                override_lock=lock,
                db_conn=db_conn,
                use_convergence_gate=True,
                dwell_s=0.0,
                max_idle_timeouts=1,
            )
            dispatcher.run()

            # Verify convergence events were recorded
            convergence_events = query_convergence_events(db_conn)
            assert len(convergence_events) > 0

            # Each convergence event should be marked as converged (stub returns True)
            for ce in convergence_events:
                assert ce["converged"] == 1
                assert ce["duration_ms"] == 0.0

            # Link events should also be recorded
            link_events = query_link_events(db_conn)
            assert len(link_events) > 0

        finally:
            stub_proc.terminate()
            stub_proc.wait(timeout=5)


class TestConvergenceStub:
    def test_convergence_stub_responds(self):
        """Convergence gate stub responds correctly to requests."""
        from nodalarc.models.link_events import LinkUp
        from nodalarc.models.metrics import ConvergenceRequest, ConvergenceResult
        from nodalarc.zmq_channels import mi_convergence_gate_connect

        stub_proc = subprocess.Popen(
            [sys.executable, "-m", "measurement.stubs.convergence_stub"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)

        try:
            ctx = zmq.Context()
            sock = ctx.socket(zmq.REQ)
            sock.connect(mi_convergence_gate_connect())
            sock.setsockopt(zmq.RCVTIMEO, 5000)

            now = datetime.now(timezone.utc)
            link_event = LinkUp(
                sim_time=now, wall_time=now,
                node_a="sat-P00S00", node_b="sat-P00S01",
                interface_a="isl0", interface_b="isl0",
                latency_ms=3.0, bandwidth_mbps=1000.0,
                reason="vis_gained",
            )
            req = ConvergenceRequest(
                event_id="test-001",
                link_event=link_event,
            )
            sock.send(req.model_dump_json().encode())
            raw = sock.recv()
            result = ConvergenceResult.model_validate_json(raw)

            assert result.event_id == "test-001"
            assert result.converged is True
            assert result.duration_ms == 0.0

            sock.close()
            ctx.term()
        finally:
            stub_proc.terminate()
            stub_proc.wait(timeout=5)
