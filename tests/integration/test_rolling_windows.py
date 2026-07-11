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
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.orbital import elements_from_params_for_radius
from nodalarc.propagator import orbital_period_for_body

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _custom_example_period_s() -> float:
    body_frame = EARTH_TEST_BODY_FRAME
    elements = elements_from_params_for_radius(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=0.0,
        true_anomaly_deg=0.0,
        radius_km=body_frame.equatorial_radius_km,
    )
    return orbital_period_for_body(elements, body_frame)


@pytest.fixture
def ring_timeline(tmp_path):
    """Generate a canonical 550 km LEO timeline."""
    import tempfile

    import yaml
    from ome.main import run as ome_run

    session = load_configuration_yaml(
        (PROJECT_ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml").read_text(
            encoding="utf-8"
        )
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as f:
        yaml.safe_dump(session, f, sort_keys=False)
        session_path = f.name

    path = ome_run(session_path, str(tmp_path), run_id="test-rolling-window")
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
    def test_timeline_starts_at_zero(self, ring_timeline):
        """Timeline begins at t=0."""
        events = _load_events(ring_timeline)
        assert events[0]["timestamp_s"] == 0.0

    def test_timeline_ends_at_orbital_period(self, ring_timeline):
        """Timeline duration matches the resolved body-frame orbital period."""
        events = _load_events(ring_timeline)
        expected_period = _custom_example_period_s()
        last_timestamp = max(e["timestamp_s"] for e in events)
        # Allow ±1 step tolerance.
        assert abs(last_timestamp - expected_period) < 2.0, (
            f"Last timestamp {last_timestamp:.1f}s should match "
            f"orbital period {expected_period:.1f}s"
        )

    def test_single_window_covers_full_period(self, ring_timeline):
        """Clock ticks span from t=0 to t~=period without gaps > step_seconds."""
        events = _load_events(ring_timeline)
        clock_ticks = [e for e in events if e["event_type"] == "ClockTick"]
        timestamps = sorted(e["timestamp_s"] for e in clock_ticks)

        assert len(timestamps) > 100  # Should have many ticks
        assert timestamps[0] == 0.0
        # Check no gaps larger than step_seconds + small tolerance
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert gap <= 1.5, f"Gap of {gap}s at t={timestamps[i]}s exceeds step_seconds"

    def test_events_span_full_duration(self, ring_timeline):
        """All event types occur across the full timeline duration."""
        events = _load_events(ring_timeline)
        period = _custom_example_period_s()

        # Clock ticks should span from 0 to ~period
        clock_ts = [e["timestamp_s"] for e in events if e["event_type"] == "ClockTick"]
        assert min(clock_ts) == 0.0
        assert max(clock_ts) >= period - 2.0

        # Visibility events are transition events, not per-tick samples. Their
        # count is therefore a geometry result, not the rolling-window contract:
        # This only requires enough transitions to prove state changes are
        # present across the window; ClockTicks above prove dense coverage.
        vis_ts = [e["timestamp_s"] for e in events if e["event_type"] == "VisibilityEvent"]
        assert len(vis_ts) >= 2, f"Expected multiple VisibilityEvents, got {len(vis_ts)}"
        assert min(vis_ts) < period * 0.1, "No early VisibilityEvents"
        assert max(vis_ts) > period * 0.5, "No late VisibilityEvents"
