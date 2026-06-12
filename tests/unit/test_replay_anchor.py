# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Bounded-replay anchor: anchor-plus-gap must equal full replay, exactly.

Recovery correctness rests on one equality: replaying from an anchor at
step A to step M produces bit-for-bit the state and events that a full
replay from step zero produces. The anchor travels THROUGH the wire
codec in these tests (encode, then decode), so the equality covers the
serialized form, not just the in-memory capture. The teeth test proves
the equality has teeth: dropping a seeded field makes it fail.
"""

from __future__ import annotations

import yaml
from nodalarc.models.session import resolve_session_epoch
from nodalarc.scheduling_checkpoint import (
    decode_retained_replay_anchor,
    encode_retained_replay_anchor,
)
from ome.event_stream import build_step_context, compute_step
from ome.main import _effective_ground_scheduling_for_runtime, _load_session_config
from ome.replay_anchor import build_replay_anchor, replay_state_from_anchor

from tests.conftest import build_segment_session_dict

ANCHOR_STEP = 20
FINAL_STEP = 40


def _session_ctx(tmp_path):
    session_path = tmp_path / "anchor-equality.yaml"
    session_path.write_text(
        yaml.dump(
            build_segment_session_dict(
                name="anchor-equality",
                constellation="configs/constellations/demo-36.yaml",
                ground_stations="configs/ground-stations/sets/demo.yaml",
                protocol="ospf",
                orbit_propagator="j2-mean-elements",
            ),
            sort_keys=False,
        )
    )
    cfg = _load_session_config(str(session_path), run_id="run-anchor-equality")
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
    step_seconds = int(cfg.resolved.time.step_seconds)
    ground_ids = frozenset(ctx.gs_mbb_overlap_ticks)
    return ctx, epoch_unix, step_seconds, ground_ids


def _run(ctx, epoch_unix, step_seconds, *, start, end, isl, gs, assoc, td):
    """Drive compute_step over [start, end], returning gap events + state."""
    dwell: dict = {}
    events = []
    result = None
    for step in range(start, end + 1):
        result = compute_step(
            ctx,
            epoch_unix,
            step,
            step_seconds,
            0.0,
            isl,
            gs,
            assoc,
            td,
            dwell_state=dwell,
        )
        assoc = result.associations
        td = result.pending_teardowns
        events.extend((step, e.event_type, e.data.model_dump_json()) for e in result.events)
    return events, isl, gs, assoc, td, result


def _full_run_with_anchor(tmp_path, ground_ids_override=None):
    """Full replay 0..FINAL, capturing the anchor at ANCHOR_STEP through
    the wire codec. Returns (anchor, gap events, final state tuple)."""
    ctx, epoch_unix, step_seconds, ground_ids = _session_ctx(tmp_path)
    isl: dict = {}
    gs: dict = {}
    assoc: dict = {}
    td: dict = {}
    dwell: dict = {}
    anchor = None
    gap_events = []
    result = None
    for step in range(FINAL_STEP + 1):
        result = compute_step(
            ctx, epoch_unix, step, step_seconds, 0.0, isl, gs, assoc, td, dwell_state=dwell
        )
        assoc = result.associations
        td = result.pending_teardowns
        if step == ANCHOR_STEP:
            wire = encode_retained_replay_anchor(
                build_replay_anchor(
                    epoch_id=0,
                    step=step,
                    isl_state=isl,
                    gs_state=gs,
                    associations=assoc,
                    teardowns=td,
                    ground_station_ids=ground_ids_override or ground_ids,
                    written_at=123.0,
                )
            )
            anchor = decode_retained_replay_anchor(wire)
        if step > ANCHOR_STEP:
            gap_events.extend((step, e.event_type, e.data.model_dump_json()) for e in result.events)
    return ctx, epoch_unix, step_seconds, anchor, gap_events, (isl, gs, assoc, td, result)


def test_anchor_plus_gap_replay_equals_full_replay(tmp_path):
    ctx, epoch_unix, step_seconds, anchor, full_gap_events, full_final = _full_run_with_anchor(
        tmp_path
    )
    assert anchor is not None and anchor.step == ANCHOR_STEP
    assert anchor.gs_state, "anchor captured no ground state — scenario has no churn"

    isl, gs, assoc, td = replay_state_from_anchor(anchor)
    gap_events, isl, gs, assoc, td, result = _run(
        ctx,
        epoch_unix,
        step_seconds,
        start=ANCHOR_STEP + 1,
        end=FINAL_STEP,
        isl=isl,
        gs=gs,
        assoc=assoc,
        td=td,
    )

    full_isl, full_gs, full_assoc, full_td, full_result = full_final
    assert gap_events == full_gap_events  # every event of the gap, byte-equal
    assert isl == full_isl
    assert gs == full_gs
    assert assoc == full_assoc
    assert td == full_td
    # The recovery-published snapshot is built from the final StepResult:
    # its authoritative source must match too.
    assert result.link_snapshot_source.ground_state == full_result.link_snapshot_source.ground_state
    assert result.link_snapshot_source.isl_state == full_result.link_snapshot_source.isl_state
    assert result.sim_time == full_result.sim_time


def test_anchor_equality_has_teeth(tmp_path):
    """Dropping a seeded field must break the equality — otherwise the
    test above could pass vacuously. gs_state carries the ground
    event-diff baseline; seeding it empty fabricates a different event
    history for the gap."""
    ctx, epoch_unix, step_seconds, anchor, full_gap_events, _full_final = _full_run_with_anchor(
        tmp_path
    )
    isl, _gs, assoc, td = replay_state_from_anchor(anchor)
    gap_events, *_ = _run(
        ctx,
        epoch_unix,
        step_seconds,
        start=ANCHOR_STEP + 1,
        end=FINAL_STEP,
        isl=isl,
        gs={},  # deliberately dropped
        assoc=assoc,
        td=td,
    )
    assert gap_events != full_gap_events


def test_anchor_round_trips_through_codec(tmp_path):
    _ctx, _epoch, _ss, anchor, _events, _final = _full_run_with_anchor(tmp_path)
    decoded = decode_retained_replay_anchor(encode_retained_replay_anchor(anchor))
    assert decoded == anchor


def test_incompatible_anchor_schema_decodes_to_none():
    import gzip
    import json

    payload = gzip.compress(json.dumps({"step": "not-an-int", "epoch_id": 0}).encode())
    assert decode_retained_replay_anchor(payload) is None
