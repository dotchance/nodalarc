"""Integration test: End-to-end OME pipeline verification.

PRD Appendix B: runs ome.main.run() with reference constellations,
verifies timeline contains ClockTick/Snapshot/VisibilityEvent events,
verifies terminal exhaustion events, verifies JSON Lines round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nodalarc.models.events import (
    ClockTick,
    TimelinePositionSnapshot,
    VisibilityEvent,
)

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def four_node_session_path():
    """Create a temporary session config for custom-example constellation."""
    import tempfile

    import yaml

    session = {
        "session": {"name": "custom-example-test"},
        "constellation": "configs/constellations/custom-example.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "protocol": "isis",
            "extensions": ["sr"],
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"step_seconds": 10},
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as f:
        yaml.dump(session, f)
        return f.name


@pytest.fixture
def sample_session_path():
    path = PROJECT_ROOT / "configs/sessions/iridium-small-36-isis-flat.yaml"
    if not path.exists():
        pytest.skip("iridium-small-36-isis-flat not available")
    return str(path)


@pytest.fixture
def polar_seam_session_path():
    """Create a temporary session config for iridium-66."""
    import tempfile

    import yaml

    session = {
        "session": {"name": "iridium-66-test"},
        "constellation": "configs/constellations/iridium-66.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "protocol": "isis",
            "extensions": ["sr"],
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"step_seconds": 10},
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as f:
        yaml.dump(session, f)
        return f.name


@pytest.fixture
def four_node_timeline(four_node_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(four_node_session_path, str(tmp_path))
    Path(four_node_session_path).unlink(missing_ok=True)
    return path


@pytest.fixture
def sample_timeline(sample_session_path, tmp_path):
    from ome.main import run as ome_run

    return ome_run(sample_session_path, str(tmp_path))


@pytest.fixture
def polar_seam_timeline(polar_seam_session_path, tmp_path):
    from ome.main import run as ome_run

    path = ome_run(polar_seam_session_path, str(tmp_path))
    Path(polar_seam_session_path).unlink(missing_ok=True)
    return path


def _load_events(path):
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


class TestFourNodePipeline:
    def test_timeline_contains_clock_ticks(self, four_node_timeline):
        events = _load_events(four_node_timeline)
        types = {e["event_type"] for e in events}
        assert "ClockTick" in types

    def test_timeline_contains_snapshots(self, four_node_timeline):
        events = _load_events(four_node_timeline)
        types = {e["event_type"] for e in events}
        assert "Snapshot" in types

    def test_timeline_contains_visibility_events(self, four_node_timeline):
        events = _load_events(four_node_timeline)
        types = {e["event_type"] for e in events}
        assert "VisibilityEvent" in types

    def test_all_events_deserialize(self, four_node_timeline):
        """All events deserialize to their respective Pydantic models."""
        events = _load_events(four_node_timeline)
        for e in events:
            if e["event_type"] == "ClockTick":
                ClockTick.model_validate(e["data"])
            elif e["event_type"] == "Snapshot":
                TimelinePositionSnapshot.model_validate(e["data"])
            elif e["event_type"] == "VisibilityEvent":
                VisibilityEvent.model_validate(e["data"])

    def test_isl_visibility_events_present(self, four_node_timeline):
        """custom-example has ISL visibility events (4 sats, 2 planes)."""
        events = _load_events(four_node_timeline)
        isl_vis = [
            e
            for e in events
            if e["event_type"] == "VisibilityEvent" and e["data"]["elevation_deg"] is None
        ]
        assert len(isl_vis) > 0

    def test_jsonl_write_read_round_trip(self, four_node_timeline, tmp_path):
        """Write → Read produces identical data."""
        from ome.event_stream import read_timeline_jsonl

        events = _load_events(four_node_timeline)
        round_tripped = read_timeline_jsonl(four_node_timeline)
        assert len(round_tripped) == len(events)
        for orig, rt in zip(events, round_tripped):
            assert orig["event_type"] == rt["event_type"]
            assert orig["timestamp_s"] == rt["timestamp_s"]


class TestStarlinkMiniTerminalExhaustion:
    def test_ground_terminal_exhaustion(self, sample_timeline):
        """Starlink-mini produces visible=True, scheduled=False ground events.

        With 60 sats and 7 ground stations (1 terminal each), multiple sats
        will be visible to a GS simultaneously, producing terminal exhaustion.
        """
        events = _load_events(sample_timeline)
        gs_exhaustion = [
            e
            for e in events
            if e["event_type"] == "VisibilityEvent"
            and e["data"]["visible"]
            and not e["data"]["scheduled"]
            and e["data"]["elevation_deg"] is not None
        ]
        assert len(gs_exhaustion) > 0, (
            "Expected visible=True, scheduled=False ground events for terminal exhaustion"
        )


class TestPolarSeamDropouts:
    def test_cross_plane_isl_dropouts(self, polar_seam_timeline):
        """Polar-seam-demo produces cross-plane ISL dropouts at high latitudes.

        Walker-star with polar_seam.enabled=True should cause cross-plane ISL
        links to drop when satellites cross the polar seam threshold.
        """
        events = _load_events(polar_seam_timeline)
        isl_vis = [
            e
            for e in events
            if e["event_type"] == "VisibilityEvent" and e["data"]["elevation_deg"] is None
        ]
        # There should be ISL state changes (visible=True then visible=False)
        visible_isls = [e for e in isl_vis if e["data"]["visible"]]
        invisible_isls = [e for e in isl_vis if not e["data"]["visible"]]
        assert len(visible_isls) > 0, "Expected ISL visibility gain events"
        assert len(invisible_isls) > 0, "Expected ISL dropout events (polar seam)"
