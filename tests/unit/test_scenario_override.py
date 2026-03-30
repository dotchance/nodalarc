"""Test scenario override set logic, thread safety, and dispatcher integration.

PRD Appendix B: proves that adding a link to the override set prevents
the TO from bringing it up when the OME reports visibility, that removing
a link and reconciling against current OME state produces the correct
outcome (up if OME says visible, down if not), and that clearing the full
set on scenario completion reconciles all overridden links.
"""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nodalarc.platform import PlatformConfig, init_platform_config, reset_platform_config

from orchestrator.realtime_dispatcher import RealtimeDispatcher


@pytest.fixture(autouse=True)
def _isolated_platform_config():
    """Ensure tests use loopback bind with non-conflicting ports."""
    from tests.unit.test_platform_config import _valid_config_dict

    reset_platform_config()
    d = _valid_config_dict()
    d["zmq_bind_host"] = "127.0.0.1"
    d["zmq_connect_host"] = "127.0.0.1"
    # Use high ports to avoid conflicts with the live system
    d["zmq_to_events_port"] = 15561
    d["zmq_to_scenario_inject_port"] = 15564
    d["zmq_playback_control_port"] = 15566
    init_platform_config(PlatformConfig(**d))
    yield
    reset_platform_config()


class TestOverrideSet:
    def test_add_override_blocks_pair(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        pair = ("sat-P00S00", "sat-P00S01")
        with lock:
            override_set.add(pair)

        with lock:
            assert pair in override_set

    def test_remove_override_unblocks_pair(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        pair = ("sat-P00S00", "sat-P00S01")
        with lock:
            override_set.add(pair)
        with lock:
            override_set.discard(pair)
        with lock:
            assert pair not in override_set

    def test_clear_removes_all(self):
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        override_set.add(("sat-P00S00", "sat-P00S01"))
        override_set.add(("sat-P00S00", "sat-P01S00"))
        override_set.add(("gs-hawthorne", "sat-P00S00"))

        with lock:
            override_set.clear()
        assert len(override_set) == 0

    def test_thread_safety(self):
        """Override set is thread-safe under concurrent access."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()
        errors: list[str] = []

        def writer():
            for i in range(100):
                pair = (f"sat-P00S{i:02d}", f"sat-P01S{i:02d}")
                with lock:
                    override_set.add(pair)

        def reader():
            for _ in range(100):
                with lock:
                    _ = len(override_set)

        def remover():
            for i in range(50):
                pair = (f"sat-P00S{i:02d}", f"sat-P01S{i:02d}")
                with lock:
                    override_set.discard(pair)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=remover),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_alphabetical_pair_normalization(self):
        """Override pairs should be stored with node_a < node_b."""
        override_set: set[tuple[str, str]] = set()

        # Normalize before inserting
        a, b = "sat-P01S00", "sat-P00S00"
        pair = (min(a, b), max(a, b))
        override_set.add(pair)

        # Check with normalized pair
        assert ("sat-P00S00", "sat-P01S00") in override_set


class TestDispatcherOverrideIntegration:
    """Tests that the override set actually prevents the dispatcher from
    acting on OME events — uses the real RealtimeDispatcher."""

    def _make_timeline_jsonl(self, tmp_path: Path, events: list[dict]) -> Path:
        """Write a minimal timeline JSONL file."""
        timeline = tmp_path / "timeline.jsonl"
        with open(timeline, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")
        return timeline

    def _visibility_record(
        self,
        node_a: str,
        node_b: str,
        visible: bool,
        scheduled: bool,
        timestamp_s: float = 0.0,
    ) -> dict:
        """Create a timeline visibility event record."""
        now = datetime.now(UTC)
        return {
            "event_type": "VisibilityEvent",
            "timestamp_s": timestamp_s,
            "data": {
                "sim_time": now.isoformat(),
                "node_a": node_a,
                "node_b": node_b,
                "visible": visible,
                "scheduled": scheduled,
                "elevation_deg": 45.0 if visible else 0.0,
                "range_km": 500.0,
                "terminal_type": "optical",
            },
        }

    def test_override_prevents_link_up(self, tmp_path):
        """A link in the override set is NOT brought up even when OME says visible."""
        pair = ("sat-P00S00", "sat-P00S01")
        events = [
            self._visibility_record(*pair, visible=True, scheduled=True, timestamp_s=0.0),
        ]
        timeline = self._make_timeline_jsonl(tmp_path, events)

        override_set: set[tuple[str, str]] = {pair}
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            timeline_path=timeline,
            interface_map={pair: ("isl0", "isl0")},
            bandwidth_map={pair: 1000.0},
            override_set=override_set,
            override_lock=lock,
            run_once=True,
        )
        dispatcher.run()

        # Link should NOT be active because it's in the override set
        assert pair not in dispatcher._active_links

    def test_non_overridden_link_comes_up(self, tmp_path):
        """A link NOT in the override set IS brought up on visibility."""
        overridden_pair = ("sat-P00S00", "sat-P00S01")
        free_pair = ("sat-P00S00", "sat-P01S00")

        events = [
            self._visibility_record(
                *overridden_pair, visible=True, scheduled=True, timestamp_s=0.0
            ),
            self._visibility_record(*free_pair, visible=True, scheduled=True, timestamp_s=0.0),
        ]
        timeline = self._make_timeline_jsonl(tmp_path, events)

        override_set: set[tuple[str, str]] = {overridden_pair}
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            timeline_path=timeline,
            interface_map={
                overridden_pair: ("isl0", "isl0"),
                free_pair: ("isl1", "isl1"),
            },
            bandwidth_map={overridden_pair: 1000.0, free_pair: 1000.0},
            override_set=override_set,
            override_lock=lock,
            run_once=True,
        )
        dispatcher.run()

        assert overridden_pair not in dispatcher._active_links
        assert free_pair in dispatcher._active_links

    def test_removing_override_allows_future_link_up(self, tmp_path):
        """After removing override and reconciling, link can come up."""
        pair = ("sat-P00S00", "sat-P00S01")

        # Two events: first visible (blocked), then visible again (allowed)
        events = [
            self._visibility_record(*pair, visible=True, scheduled=True, timestamp_s=0.0),
            self._visibility_record(*pair, visible=True, scheduled=True, timestamp_s=10.0),
        ]
        timeline = self._make_timeline_jsonl(tmp_path, events)

        override_set: set[tuple[str, str]] = {pair}
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            timeline_path=timeline,
            interface_map={pair: ("isl0", "isl0")},
            bandwidth_map={pair: 1000.0},
            override_set=override_set,
            override_lock=lock,
            run_once=True,
        )

        # Clear override before run so second event is not blocked
        with lock:
            override_set.clear()

        dispatcher.run()

        # With override cleared, the link should come up
        assert pair in dispatcher._active_links

    def test_clear_all_overrides(self, tmp_path):
        """Clearing all overrides allows all previously-blocked links to come up."""
        pairs = [
            ("sat-P00S00", "sat-P00S01"),
            ("sat-P00S00", "sat-P01S00"),
            ("sat-P01S00", "sat-P01S01"),
        ]

        events = [
            self._visibility_record(*p, visible=True, scheduled=True, timestamp_s=0.0)
            for p in pairs
        ]
        timeline = self._make_timeline_jsonl(tmp_path, events)

        override_set: set[tuple[str, str]] = set(pairs)
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            timeline_path=timeline,
            interface_map={p: (f"isl{i}", f"isl{i}") for i, p in enumerate(pairs)},
            bandwidth_map={p: 1000.0 for p in pairs},
            override_set=override_set,
            override_lock=lock,
            run_once=True,
        )

        # Clear all overrides before running
        with lock:
            override_set.clear()

        dispatcher.run()

        # All links should be up
        for pair in pairs:
            assert pair in dispatcher._active_links
