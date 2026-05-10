"""Integration test: Rolling window verification.

PRD Appendix B: Since rolling windows are deferred (single-window approach),
verify:
- Single window covers full orbital period
- Timeline duration matches orbital_period() for the constellation
- Events span from t=0 to t=period
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ome.propagator import orbital_period

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def four_node_timeline(tmp_path):
    """Generate custom-example timeline."""
    import tempfile

    import yaml
    from ome.main import run as ome_run

    session = {
        "session": {"name": "rolling-window-test"},
        "constellation": "configs/constellations/custom-example.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "protocol": "isis",
            "extensions": ["sr"],
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"step_seconds": 1},
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as f:
        yaml.dump(session, f)
        session_path = f.name

    path = ome_run(session_path, str(tmp_path))
    Path(session_path).unlink(missing_ok=True)
    return path


def _load_events(path):
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


class TestSingleWindowCoverage:
    def test_timeline_starts_at_zero(self, four_node_timeline):
        """Timeline begins at t=0."""
        events = _load_events(four_node_timeline)
        assert events[0]["timestamp_s"] == 0.0

    def test_timeline_ends_at_orbital_period(self, four_node_timeline):
        """Timeline duration matches orbital_period() for 550 km altitude."""
        events = _load_events(four_node_timeline)
        expected_period = orbital_period(550.0)
        last_timestamp = max(e["timestamp_s"] for e in events)
        # Allow ±1 step tolerance (step_seconds=1 for custom-example)
        assert abs(last_timestamp - expected_period) < 2.0, (
            f"Last timestamp {last_timestamp:.1f}s should match "
            f"orbital period {expected_period:.1f}s"
        )

    def test_single_window_covers_full_period(self, four_node_timeline):
        """Clock ticks span from t=0 to t~=period without gaps > step_seconds."""
        events = _load_events(four_node_timeline)
        clock_ticks = [e for e in events if e["event_type"] == "ClockTick"]
        timestamps = sorted(e["timestamp_s"] for e in clock_ticks)

        assert len(timestamps) > 100  # Should have many ticks
        assert timestamps[0] == 0.0
        # Check no gaps larger than step_seconds + small tolerance
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert gap <= 1.5, f"Gap of {gap}s at t={timestamps[i]}s exceeds step_seconds"

    def test_events_span_full_duration(self, four_node_timeline):
        """All event types occur across the full timeline duration."""
        events = _load_events(four_node_timeline)
        period = orbital_period(550.0)

        # Clock ticks should span from 0 to ~period
        clock_ts = [e["timestamp_s"] for e in events if e["event_type"] == "ClockTick"]
        assert min(clock_ts) == 0.0
        assert max(clock_ts) >= period - 2.0

        # Visibility events are transition events, not per-tick samples. Their
        # count is therefore a geometry result, not the rolling-window contract:
        # custom-example currently produces nine ISL gain/loss transitions over
        # one orbit with the two-station us-conus ground set. This test only
        # requires enough transitions to prove state changes are present across
        # the window; ClockTicks above prove dense period coverage.
        vis_ts = [e["timestamp_s"] for e in events if e["event_type"] == "VisibilityEvent"]
        assert len(vis_ts) >= 2, f"Expected multiple VisibilityEvents, got {len(vis_ts)}"
        assert min(vis_ts) < period * 0.1, "No early VisibilityEvents"
        assert max(vis_ts) > period * 0.5, "No late VisibilityEvents"
