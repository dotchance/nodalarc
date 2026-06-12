# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Equivalence proof for the dwell pass-frontier walker.

The production estimator carries a per-pair pass frontier across ticks so
it samples at most one new future tick per pair per call; the retained
exhaustive walker re-samples the full horizon every call. Both share the
same per-pair physics helper, so they may differ only in WHICH ticks they
sample — these tests prove they never differ in WHAT they return, by
running both side by side on every tick of real sessions and asserting
exact equality (same arithmetic, same floats — no tolerance).

The synthetic fast-pass scenario additionally ENFORCES that the hard
paths were exercised: pass closures discovered (first_invisible found),
closed pairs answered from the memo, and invalidation when candidacy
returns after a pass ends. Coverage is asserted, not hoped for.
"""

from __future__ import annotations

from pathlib import Path

import ome.ground_visibility_engine as gve
import pytest
import yaml
from ome.event_stream import build_step_context, compute_step

from tests.conftest import build_segment_session_dict


class _Comparator:
    """Monkeypatched in place of the production estimator: runs the oracle
    beside production with identical inputs and asserts exact equality."""

    def __init__(self):
        self.production = gve._estimate_remaining_visible_seconds
        self.ticks_compared = 0
        self.pairs_compared = 0
        self.closures_recorded: dict[tuple[str, str], int] = {}
        self.memo_answered_closed_pair = False
        self.re_risen_pairs: set[tuple[str, str]] = set()

    def __call__(self, *, candidates, gs_positions, gs_min_elevations, lookahead, dwell_state=None):
        # Re-rise: a pair whose recorded pass end is now in the past shows
        # up as a candidate again — the memo entry must be discarded, and
        # the result must still match a fresh oracle walk.
        for pair in candidates:
            closed_at = self.closures_recorded.get(pair)
            if closed_at is not None and lookahead.step >= closed_at:
                self.re_risen_pairs.add(pair)
        # A closed pair answered without any walk (pass end known, still
        # in the future) is the pure-memo path.
        if dwell_state:
            for pair in candidates:
                entry = dwell_state.get(pair)
                if entry is not None and entry.first_invisible is not None:
                    self.memo_answered_closed_pair = True

        expected = gve._estimate_remaining_visible_seconds_exhaustive(
            candidates=candidates,
            gs_positions=gs_positions,
            gs_min_elevations=gs_min_elevations,
            lookahead=lookahead,
        )
        got = self.production(
            candidates=candidates,
            gs_positions=gs_positions,
            gs_min_elevations=gs_min_elevations,
            lookahead=lookahead,
            dwell_state=dwell_state,
        )
        assert got == expected, (
            f"frontier walker diverged from oracle at step {lookahead.step}: "
            f"{ {p: (got[p], expected[p]) for p in got if got[p] != expected[p]} }"
        )
        self.ticks_compared += 1
        self.pairs_compared += len(candidates)
        if dwell_state:
            for pair, entry in dwell_state.items():
                if entry.first_invisible is not None:
                    self.closures_recorded[pair] = entry.first_invisible
        return got


def _run_session_with_comparator(monkeypatch, ctx, *, epoch_unix, step_seconds, steps):
    comparator = _Comparator()
    monkeypatch.setattr(gve, "_estimate_remaining_visible_seconds", comparator)
    isl_state: dict = {}
    gs_state: dict = {}
    associations: dict = {}
    teardowns: dict = {}
    dwell_state: dict = {}
    for step in range(steps):
        result = compute_step(
            ctx,
            epoch_unix,
            step,
            step_seconds,
            0.0,
            isl_state,
            gs_state,
            associations,
            teardowns,
            dwell_state=dwell_state,
        )
        associations = result.associations
        teardowns = result.pending_teardowns
    return comparator


def _ctx_from_session_file(session_path):
    from nodalarc.models.session import resolve_session_epoch
    from ome.main import _effective_ground_scheduling_for_runtime, _load_session_config

    cfg = _load_session_config(session_path, run_id="run-dwell-equiv-0001")
    ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )
    epoch_unix = resolve_session_epoch(cfg.resolved.time)
    return ctx, int(cfg.resolved.time.step_seconds), epoch_unix


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name,steps", [("earth-geo-tdrs", 20), ("earth-meo-gps", 8)])
def test_frontier_matches_oracle_on_catalog_dwell_sessions(monkeypatch, name, steps):
    """Continuously-visible regimes: frontier extension + horizon caps."""
    ctx, step_seconds, epoch_unix = _ctx_from_session_file(
        REPO_ROOT / "catalog" / "nodalarc" / "sessions" / f"{name}.yaml"
    )
    comparator = _run_session_with_comparator(
        monkeypatch, ctx, epoch_unix=epoch_unix, step_seconds=step_seconds, steps=steps
    )
    assert comparator.ticks_compared == steps
    assert comparator.pairs_compared > 0


def test_frontier_matches_oracle_through_pass_closures(monkeypatch, tmp_path):
    """Fast LEO passes under a dwell policy: closures, memo answers for
    closed pairs, and re-rise invalidation — coverage asserted."""
    raw = build_segment_session_dict(
        name="dwell-equivalence-leo",
        constellation="configs/constellations/demo-36.yaml",
        ground_stations="configs/ground-stations/sets/demo.yaml",
        orbit_propagator="j2-mean-elements",
        scheduling={
            "selection_policy": {"longest_remaining_pass": {"lookahead_horizon_ticks": 30}},
            "handover_policy": {"hard_release": {}},
            "handover_mode": "bbm",
            "mbb_overlap_ticks": 0,
            "mbb_reserve": 0,
        },
        time={"step_seconds": 10},
    )
    session_path = tmp_path / "dwell-equivalence-leo.yaml"
    session_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    ctx, step_seconds, epoch_unix = _ctx_from_session_file(session_path)
    comparator = _run_session_with_comparator(
        monkeypatch, ctx, epoch_unix=epoch_unix, step_seconds=step_seconds, steps=120
    )

    # The estimator is only invoked on ticks with visible dwell candidates.
    assert comparator.ticks_compared > 100
    assert comparator.closures_recorded, (
        "scenario never discovered a pass end — the closure path was not "
        "exercised; shrink the horizon or lengthen the run"
    )
    assert comparator.memo_answered_closed_pair, (
        "no tick answered a closed pair from the memo — the memo-read path was not exercised"
    )
    # Same-pair re-rise takes ~an orbital period and is not guaranteed in a
    # short run; the invalidation path is pinned deterministically below.


class _InputCapture:
    def __init__(self):
        self.production = gve._estimate_remaining_visible_seconds
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.production(**kwargs)


def test_stale_memo_entries_are_discarded_and_rewalked(monkeypatch):
    """Entries whose pass ended (first_invisible <= now) or whose frontier
    fell behind (coverage gap) must be discarded — the pair is re-walked
    fresh and the result still matches the oracle exactly."""
    ctx, step_seconds, epoch_unix = _ctx_from_session_file(
        REPO_ROOT / "catalog" / "nodalarc" / "sessions" / "earth-geo-tdrs.yaml"
    )
    capture = _InputCapture()
    monkeypatch.setattr(gve, "_estimate_remaining_visible_seconds", capture)
    compute_step(ctx, epoch_unix, 3, step_seconds, 0.0, {}, {}, {}, {})
    assert capture.calls, "geo-tdrs tick 3 produced no dwell candidates"
    inputs = capture.calls[0]
    candidates = sorted(inputs["candidates"])
    assert len(candidates) >= 2
    now = inputs["lookahead"].step

    seeded = {
        # Ended pass: first invisible tick is not in the future anymore.
        candidates[0]: gve.DwellPassState(verified_visible_through=now - 1, first_invisible=now),
        # Coverage gap: frontier fell behind the current tick.
        candidates[1]: gve.DwellPassState(verified_visible_through=now - 2, first_invisible=None),
    }
    dwell_state = dict(seeded)
    got = capture.production(
        candidates=inputs["candidates"],
        gs_positions=inputs["gs_positions"],
        gs_min_elevations=inputs["gs_min_elevations"],
        lookahead=inputs["lookahead"],
        dwell_state=dwell_state,
    )
    expected = gve._estimate_remaining_visible_seconds_exhaustive(
        candidates=inputs["candidates"],
        gs_positions=inputs["gs_positions"],
        gs_min_elevations=inputs["gs_min_elevations"],
        lookahead=inputs["lookahead"],
    )
    assert got == expected
    for pair, stale in seeded.items():
        assert dwell_state[pair] != stale, f"stale entry for {pair} survived"
