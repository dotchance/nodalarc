# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Bounded-replay anchor conversions between OME state and the wire model.

Recovery replays history to rebuild in-memory state; the anchor captures
the complete replay-carried set as of one step so the replay covers only
the gap from the anchor to the checkpoint. This module owns the
conversion in both directions and the pair-key convention ("a:b", node
ids are DNS-safe and never contain colons — the same convention the
scheduling checkpoint uses for association keys).

The capture copies the mutable per-tick dicts: the pacing loop mutates
isl_state/gs_state in place every tick, and the anchor build happens on
the publisher thread later, so the captured views must be frozen at the
anchor tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.models.events import CheckpointAssociation, ReplayAnchor, TeardownEntry

from ome.types import MbbTeardown, MbbTeardownState


def _pair_key(pair: tuple[str, str]) -> str:
    return f"{pair[0]}:{pair[1]}"


def _key_pair(key: str) -> tuple[str, str]:
    node_a, _, node_b = key.partition(":")
    if not node_a or not node_b:
        raise ValueError(f"malformed replay-anchor pair key: {key!r}")
    return (node_a, node_b)


def build_replay_anchor(
    *,
    epoch_id: int,
    step: int,
    isl_state: dict[tuple[str, str], tuple[bool, bool]],
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]],
    associations: dict[tuple[str, str], tuple[int, int]],
    teardowns: MbbTeardownState,
    ground_station_ids: frozenset[str],
    written_at: float,
) -> ReplayAnchor:
    """Freeze the replay-carried state as of ``step`` into the wire model.

    Association and teardown entries reuse the checkpoint's wire shapes;
    the pair is reconstructed from gs_id/sat_id fields on restore, so the
    ground endpoint must be identified here exactly as the checkpoint
    builder does.
    """

    def _ground_sat(pair: tuple[str, str]) -> tuple[str, str]:
        in_ground = [node for node in pair if node in ground_station_ids]
        if len(in_ground) != 1:
            raise ValueError(
                f"Replay anchor pair {pair!r} must contain exactly one "
                f"ground-station endpoint, found {len(in_ground)}"
            )
        gs_id = in_ground[0]
        sat_id = pair[1] if pair[0] == gs_id else pair[0]
        return gs_id, sat_id

    assoc_flat: dict[str, CheckpointAssociation] = {}
    for pair, (gs_ti, sat_ti) in associations.items():
        gs_id, sat_id = _ground_sat(pair)
        assoc_flat[_pair_key(pair)] = CheckpointAssociation(
            gs_id=gs_id,
            sat_id=sat_id,
            gs_terminal_index=gs_ti,
            sat_terminal_index=sat_ti,
        )

    td_flat: dict[str, TeardownEntry] = {}
    for pair, teardown in teardowns.items():
        gs_id, sat_id = _ground_sat(pair)
        td_flat[_pair_key(pair)] = TeardownEntry(
            start_step=teardown.start_step,
            # remaining_ticks on the wire shape is a checkpoint-side audit
            # value; the anchor restores from start_step, which is the
            # internal source of truth the allocator computes from.
            remaining_ticks=0,
            gs_id=gs_id,
            sat_id=sat_id,
            successor_node_a=teardown.successor_pair[0],
            successor_node_b=teardown.successor_pair[1],
        )

    return ReplayAnchor(
        epoch_id=epoch_id,
        step=step,
        isl_state={_pair_key(p): v for p, v in isl_state.items()},
        gs_state={_pair_key(p): v for p, v in gs_state.items()},
        associations=assoc_flat,
        pending_teardowns=td_flat,
        written_at=written_at,
    )


def replay_state_from_anchor(
    anchor: ReplayAnchor,
) -> tuple[
    dict[tuple[str, str], tuple[bool, bool]],
    dict[tuple[str, str], tuple[bool, bool, str]],
    dict[tuple[str, str], tuple[int, int]],
    MbbTeardownState,
]:
    """Rebuild the internal replay-carried state from a decoded anchor.

    Returns (isl_state, gs_state, associations, pending_teardowns) in the
    exact shapes the pacing loop threads through compute_step.
    """
    isl_state = {_key_pair(k): (bool(v[0]), bool(v[1])) for k, v in anchor.isl_state.items()}
    gs_state = {
        _key_pair(k): (bool(v[0]), bool(v[1]), str(v[2])) for k, v in anchor.gs_state.items()
    }
    associations = {
        _key_pair(k): (entry.gs_terminal_index, entry.sat_terminal_index)
        for k, entry in anchor.associations.items()
    }
    teardowns: MbbTeardownState = {
        _key_pair(k): MbbTeardown(
            start_step=entry.start_step,
            successor_pair=(entry.successor_node_a, entry.successor_node_b),
        )
        for k, entry in anchor.pending_teardowns.items()
    }
    return isl_state, gs_state, associations, teardowns


@dataclass(frozen=True)
class DeferredReplayAnchor:
    """One anchor tick's inputs, committed by the pacer, built by the
    publisher in its sleep window — the same ownership transfer as the
    authority snapshots. isl_state/gs_state are COPIES frozen at the
    anchor tick (the pacing loop mutates the originals every tick);
    associations and teardowns are that tick's fresh result objects."""

    epoch_id: int
    step: int
    isl_state: dict
    gs_state: dict
    associations: dict
    teardowns: MbbTeardownState
    ground_station_ids: frozenset[str]
    written_at: float

    def build(self) -> ReplayAnchor:
        return build_replay_anchor(
            epoch_id=self.epoch_id,
            step=self.step,
            isl_state=self.isl_state,
            gs_state=self.gs_state,
            associations=self.associations,
            teardowns=self.teardowns,
            ground_station_ids=self.ground_station_ids,
            written_at=self.written_at,
        )
