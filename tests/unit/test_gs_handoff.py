"""Test GS terminal handoff: when a higher-elevation satellite takes over,
the old satellite's link must be torn down.

This catches the bug where visible=True, scheduled=False events were ignored
by the dispatcher, leaving stale GS links active across the globe.

Tests the dispatcher's _process_batch logic directly without ZMQ sockets.
"""

import json
import threading
from unittest.mock import MagicMock

from nodalarc.zmq_channels import decode_message, TOPIC_LINK_DOWN, TOPIC_LINK_UP
from orchestrator.realtime_dispatcher import RealtimeDispatcher


def _vis_record(timestamp_s, node_a, node_b, visible, scheduled, elevation=30.0):
    """Create a VisibilityEvent timeline record."""
    a, b = min(node_a, node_b), max(node_a, node_b)
    return {
        "timestamp_s": timestamp_s,
        "event_type": "VisibilityEvent",
        "data": {
            "sim_time": f"2026-01-01T00:00:{int(timestamp_s):02d}Z",
            "node_a": a,
            "node_b": b,
            "visible": visible,
            "scheduled": scheduled,
            "range_km": 1500.0,
            "elevation_deg": elevation,
            "terminal_type": "optical",
        },
    }


def _snap_record(timestamp_s, positions):
    """Create a Snapshot timeline record."""
    return {
        "timestamp_s": timestamp_s,
        "event_type": "Snapshot",
        "data": {
            "sim_time": f"2026-01-01T00:00:{int(timestamp_s):02d}Z",
            "timestamp_s": timestamp_s,
            "positions": {
                nid: {"lat_deg": lat, "lon_deg": lon, "alt_km": 550.0,
                       "vel_x_km_s": 0, "vel_y_km_s": 0, "vel_z_km_s": 0}
                for nid, (lat, lon) in positions.items()
            },
        },
    }


def _make_dispatcher(interface_map, bandwidth_map):
    """Create a dispatcher with mock ZMQ for unit testing."""
    import tempfile, pathlib
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    f.close()
    return RealtimeDispatcher(
        timeline_path=pathlib.Path(f.name),
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        override_set=set(),
        override_lock=threading.Lock(),
        
        
        max_idle_timeouts=1,
    )


def _gs_links(active_links):
    """Return the subset of active links involving a ground station."""
    return {
        pair for pair in active_links
        if pair[0].startswith("gs-") or pair[1].startswith("gs-")
    }


def _decode_events(pub_sock):
    """Extract all (topic, data) pairs from mock pub_sock.send() calls."""
    result = []
    for call in pub_sock.send.call_args_list:
        raw = call[0][0]
        topic, payload = decode_message(raw)
        result.append((topic, json.loads(payload)))
    return result


class TestGSHandoff:
    """Verify GS terminal handoff tears down old link."""

    def test_scheduled_false_removes_gs_link_from_active(self):
        """visible=True, scheduled=False on a GS pair must remove it from active_links."""
        interface_map = {
            ("gs-test", "sat-P00S00"): ("gnd0", "gnd0"),
        }
        bandwidth_map = {("gs-test", "sat-P00S00"): 1000.0}
        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {"gs-test": (0.0, 0.0), "sat-P00S00": (1.0, 1.0)}

        # Link up
        dispatcher._process_batch([
            _snap_record(0, positions),
            _vis_record(0, "gs-test", "sat-P00S00", True, True, 40),
        ], pub_sock, None, MagicMock())
        assert len(_gs_links(dispatcher._active_links)) == 1

        # Terminal deallocated (scheduled=False)
        dispatcher._process_batch([
            _vis_record(3, "gs-test", "sat-P00S00", True, False, 20),
        ], pub_sock, None, MagicMock())

        assert len(_gs_links(dispatcher._active_links)) == 0

    def test_scheduled_false_publishes_link_down(self):
        """Terminal deallocation must emit a LinkDown ZMQ message."""
        interface_map = {
            ("gs-test", "sat-P00S00"): ("gnd0", "gnd0"),
        }
        bandwidth_map = {("gs-test", "sat-P00S00"): 1000.0}
        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {"gs-test": (0.0, 0.0), "sat-P00S00": (1.0, 1.0)}

        dispatcher._process_batch([
            _snap_record(0, positions),
            _vis_record(0, "gs-test", "sat-P00S00", True, True, 40),
        ], pub_sock, None, MagicMock())
        pub_sock.reset_mock()

        dispatcher._process_batch([
            _vis_record(3, "gs-test", "sat-P00S00", True, False, 20),
        ], pub_sock, None, MagicMock())

        events = _decode_events(pub_sock)
        downs = [d for t, d in events if t == TOPIC_LINK_DOWN]
        assert len(downs) == 1
        # The down event must name both endpoints
        assert "gs-test" in (downs[0]["node_a"], downs[0]["node_b"])
        assert "sat-P00S00" in (downs[0]["node_a"], downs[0]["node_b"])

    def test_isl_scheduled_false_is_ignored(self):
        """For non-GS links, scheduled=False must NOT tear down the link."""
        interface_map = {
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
        }
        bandwidth_map = {("sat-P00S00", "sat-P00S01"): 1000.0}
        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {"sat-P00S00": (0.0, 0.0), "sat-P00S01": (0.0, 30.0)}

        dispatcher._process_batch([
            _snap_record(0, positions),
            _vis_record(0, "sat-P00S00", "sat-P00S01", True, True),
        ], pub_sock, None, MagicMock())
        assert len(dispatcher._active_links) == 1

        dispatcher._process_batch([
            _vis_record(3, "sat-P00S00", "sat-P00S01", True, False),
        ], pub_sock, None, MagicMock())

        # ISL must still be active
        assert len(dispatcher._active_links) == 1

    def test_only_current_gs_link_survives_after_multiple_handoffs(self):
        """After N handoffs on a single GS, exactly 1 GS link is active —
        the most recently scheduled satellite. All previous are torn down."""
        n_sats = 5
        gs = "gs-test"
        sat_ids = [f"sat-P00S{i:02d}" for i in range(n_sats)]
        interface_map = {(gs, sid): ("gnd0", "gnd0") for sid in sat_ids}
        bandwidth_map = {(gs, sid): 1000.0 for sid in sat_ids}

        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {gs: (0.0, 0.0)}
        positions.update({sid: (float(i), float(i)) for i, sid in enumerate(sat_ids)})

        # Initial snapshot
        dispatcher._process_batch([
            _snap_record(0, positions),
        ], pub_sock, None, MagicMock())

        # Sequential handoffs: each satellite takes over from the previous
        for i in range(n_sats):
            batch = [_snap_record(float((i + 1) * 10), positions)]
            if i > 0:
                # Deallocate previous
                batch.append(_vis_record(
                    float((i + 1) * 10), gs, sat_ids[i - 1],
                    visible=True, scheduled=False, elevation=10.0,
                ))
            # Allocate current
            batch.append(_vis_record(
                float((i + 1) * 10), gs, sat_ids[i],
                visible=True, scheduled=True, elevation=60.0,
            ))
            dispatcher._process_batch(batch, pub_sock, None, MagicMock())

        # Exactly 1 GS link active (the last scheduled satellite)
        gs_active = _gs_links(dispatcher._active_links)
        assert len(gs_active) == 1, (
            f"Expected exactly 1 active GS link after {n_sats} handoffs, "
            f"got {len(gs_active)}: {gs_active}"
        )

    def test_no_gs_link_survives_after_deallocation_without_replacement(self):
        """If a GS link is deallocated and no new satellite is scheduled,
        zero GS links should be active."""
        interface_map = {("gs-test", "sat-P00S00"): ("gnd0", "gnd0")}
        bandwidth_map = {("gs-test", "sat-P00S00"): 1000.0}
        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {"gs-test": (0.0, 0.0), "sat-P00S00": (1.0, 1.0)}

        dispatcher._process_batch([
            _snap_record(0, positions),
            _vis_record(0, "gs-test", "sat-P00S00", True, True, 40),
        ], pub_sock, None, MagicMock())
        assert len(_gs_links(dispatcher._active_links)) == 1

        # Satellite deallocated, nothing replaces it
        dispatcher._process_batch([
            _vis_record(10, "gs-test", "sat-P00S00", True, False, 10),
        ], pub_sock, None, MagicMock())

        assert len(_gs_links(dispatcher._active_links)) == 0

    def test_multiple_gs_handoffs_concurrent(self):
        """Two different ground stations can each do independent handoffs
        without interfering with each other."""
        gs_a, gs_b = "gs-alpha", "gs-beta"
        sat_a1, sat_a2 = "sat-P00S00", "sat-P00S01"
        sat_b1, sat_b2 = "sat-P01S00", "sat-P01S01"
        interface_map = {
            (gs_a, sat_a1): ("gnd0", "gnd0"),
            (gs_a, sat_a2): ("gnd0", "gnd0"),
            (gs_b, sat_b1): ("gnd0", "gnd0"),
            (gs_b, sat_b2): ("gnd0", "gnd0"),
        }
        bandwidth_map = {k: 1000.0 for k in interface_map}
        dispatcher = _make_dispatcher(interface_map, bandwidth_map)
        pub_sock = MagicMock()

        positions = {
            gs_a: (0.0, 0.0), gs_b: (10.0, 10.0),
            sat_a1: (1.0, 1.0), sat_a2: (2.0, 2.0),
            sat_b1: (11.0, 11.0), sat_b2: (12.0, 12.0),
        }

        # Both GS get initial satellite
        dispatcher._process_batch([
            _snap_record(0, positions),
            _vis_record(0, gs_a, sat_a1, True, True, 40),
            _vis_record(0, gs_b, sat_b1, True, True, 40),
        ], pub_sock, None, MagicMock())
        assert len(_gs_links(dispatcher._active_links)) == 2

        # Both handoff simultaneously
        dispatcher._process_batch([
            _snap_record(10, positions),
            _vis_record(10, gs_a, sat_a1, True, False, 15),
            _vis_record(10, gs_a, sat_a2, True, True, 60),
            _vis_record(10, gs_b, sat_b1, True, False, 15),
            _vis_record(10, gs_b, sat_b2, True, True, 60),
        ], pub_sock, None, MagicMock())

        gs_active = _gs_links(dispatcher._active_links)
        assert len(gs_active) == 2, (
            f"Each GS should have exactly 1 link, got {gs_active}"
        )
        # Verify it's the NEW satellites, not the old ones
        active_sats = set()
        for pair in gs_active:
            for node in pair:
                if node.startswith("sat-"):
                    active_sats.add(node)
        assert sat_a1 not in active_sats
        assert sat_b1 not in active_sats
        assert sat_a2 in active_sats
        assert sat_b2 in active_sats
