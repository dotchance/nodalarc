# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for _LookAheadThread — background window precomputation."""

from __future__ import annotations

import time


def test_lookahead_submit_and_get_result():
    """Submit a computation, verify result is retrievable."""
    from ome.main import _LookAheadThread

    la = _LookAheadThread()

    # Use a trivial computation via the real precompute_timeline_window
    from tests.unit.test_compute_step import _load_test_session

    session, cc, gs_file, sats, addressing, neighbors = _load_test_session()

    common_args = dict(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        step_seconds=session.time.step_seconds,
    )

    la.submit(
        common_args=common_args,
        epoch_unix=1704067200.0,
        duration_s=10.0,  # tiny window for speed
        initial_isl_state=None,
        initial_gs_state=None,
        timestamp_offset=0.0,
    )

    result = la.get_result(timeout=10.0)
    assert result is not None, "Look-ahead should produce a result"
    events, isl_state, gs_state, associations = result
    assert len(events) > 0, "Should produce events"
    assert isinstance(isl_state, dict)
    assert isinstance(gs_state, dict)
    assert isinstance(associations, dict)


def test_lookahead_cancel_discards_result():
    """Cancel should discard any in-flight result."""
    from ome.main import _LookAheadThread

    la = _LookAheadThread()

    from tests.unit.test_compute_step import _load_test_session

    session, cc, gs_file, sats, addressing, neighbors = _load_test_session()

    common_args = dict(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        step_seconds=session.time.step_seconds,
    )

    la.submit(
        common_args=common_args,
        epoch_unix=1704067200.0,
        duration_s=5730.0,  # full window — takes ~8s
        initial_isl_state=None,
        initial_gs_state=None,
        timestamp_offset=0.0,
    )

    # Cancel immediately
    time.sleep(0.1)
    la.cancel()

    result = la.get_result(timeout=0.5)
    assert result is None, "Cancelled computation should return None"


def test_lookahead_is_ready():
    """is_ready() should reflect computation completion."""
    from ome.main import _LookAheadThread

    la = _LookAheadThread()

    from tests.unit.test_compute_step import _load_test_session

    session, cc, gs_file, sats, addressing, neighbors = _load_test_session()

    common_args = dict(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        step_seconds=session.time.step_seconds,
    )

    assert not la.is_ready()

    la.submit(
        common_args=common_args,
        epoch_unix=1704067200.0,
        duration_s=5.0,  # very short
        initial_isl_state=None,
        initial_gs_state=None,
        timestamp_offset=0.0,
    )

    # Wait for completion
    la.get_result(timeout=10.0)
    assert la.is_ready()
