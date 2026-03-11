"""Tests for SnapshotBuilder full link state tracking."""

from datetime import datetime, timezone

from nodalarc.models.events import VisibilityEvent
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder


def _event(node_a, node_b, visible, scheduled, range_km=1000.0) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=range_km,
        elevation_deg=None,
        terminal_type="optical",
    )


def _builder() -> SnapshotBuilder:
    return SnapshotBuilder(node_registry={}, interface_map={})


def test_full_link_state_empty_initially():
    b = _builder()
    assert b.full_link_state == {}


def test_full_link_state_tracks_active_link():
    b = _builder()
    b.apply_link_event(_event("sat-P00S00", "sat-P00S01", True, True))
    state = b.full_link_state
    assert ("sat-P00S00", "sat-P00S01") in state
    visible, scheduled, _ = state[("sat-P00S00", "sat-P00S01")]
    assert visible is True
    assert scheduled is True


def test_full_link_state_tracks_visible_unscheduled():
    b = _builder()
    b.apply_link_event(_event("sat-P00S00", "sat-P00S01", True, False))
    state = b.full_link_state
    visible, scheduled, _ = state[("sat-P00S00", "sat-P00S01")]
    assert visible is True
    assert scheduled is False
    # Must NOT be in active_link_set
    assert ("sat-P00S00", "sat-P00S01") not in b.active_link_set


def test_full_link_state_tracks_invisible_link():
    b = _builder()
    b.apply_link_event(_event("sat-P00S00", "sat-P00S01", False, False))
    state = b.full_link_state
    visible, scheduled, _ = state[("sat-P00S00", "sat-P00S01")]
    assert visible is False
    assert scheduled is False


def test_full_link_state_independent_of_active_link_set():
    """full_link_state and active_link_set track different things."""
    b = _builder()
    # Link goes active, then goes invisible
    b.apply_link_event(_event("sat-P00S00", "sat-P00S01", True, True))
    b.apply_link_event(_event("sat-P00S00", "sat-P00S01", False, False))

    # active_link_set: empty (link went down)
    assert ("sat-P00S00", "sat-P00S01") not in b.active_link_set

    # full_link_state: records last known state (invisible)
    state = b.full_link_state
    visible, _, _ = state[("sat-P00S00", "sat-P00S01")]
    assert visible is False
