# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for SchedulingCheckpoint model serialization and edge cases."""

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import SchedulingCheckpoint
from nodalarc.scheduling_checkpoint import decode_retained_scheduling_checkpoint


def _checkpoint(**overrides) -> SchedulingCheckpoint:
    fields = {
        "sim_time": datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        "epoch_id": 0,
        "snapshot_seq": 1,
        "step": 0,
        "associations": {},
        "pending_teardowns": {},
        "paused": False,
        "time_accel": 1.0,
        "written_at": 1_735_689_600.0,
    }
    fields.update(overrides)
    return SchedulingCheckpoint(**fields)


def test_incompatible_retained_checkpoint_decodes_as_clean_start():
    """Old retained checkpoint schemas must not crash a branch deployment."""
    import gzip
    import json

    old_schema = {
        "sim_time": "2025-01-01T00:00:00+00:00",
        "epoch_id": 0,
        "snapshot_seq": 99,
        "step": 42,
        "associations": {"gs-london": "sat-001"},
        "pending_teardowns": {
            "gs-london:sat-099": {
                "remaining_ticks": 2,
                "gs_id": "gs-london",
                "sat_id": "sat-099",
            }
        },
    }

    payload = gzip.compress(json.dumps(old_schema).encode())
    assert decode_retained_scheduling_checkpoint(payload) is None


def test_recovered_checkpoint_accepts_extended_wall_clock_gap():
    """A valid checkpoint remains the simulation-lineage authority after downtime."""
    from ome.main import _validate_recovered_checkpoint

    ckpt = _checkpoint(written_at=1_000.0, step=4, snapshot_seq=8)

    assert _validate_recovered_checkpoint(ckpt, now_wall_s=1_900.0) == 900.0


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("written_at", 0.0, "invalid written_at"),
        ("step", -1, "negative step"),
        ("snapshot_seq", 0, "invalid snapshot_seq"),
    ],
)
def test_recovered_checkpoint_rejects_invalid_lineage_fields(field, value, match):
    from ome.main import _validate_recovered_checkpoint

    ckpt = _checkpoint(**{field: value})

    with pytest.raises(RuntimeError, match=match):
        _validate_recovered_checkpoint(ckpt, now_wall_s=2_000.0)


def test_recovered_checkpoint_rejects_future_written_at():
    from ome.main import _validate_recovered_checkpoint

    ckpt = _checkpoint(written_at=2_000.0)

    with pytest.raises(RuntimeError, match="future"):
        _validate_recovered_checkpoint(ckpt, now_wall_s=1_999.0)


def test_checkpoint_pair_roles_are_derived_from_ground_universe():
    """Allocator-normalized pairs can be sat-first; checkpoint roles cannot."""
    from ome.main import _checkpoint_ground_sat_pair

    assert _checkpoint_ground_sat_pair(
        ("luna-sat-p01s00", "lunar-ground-gs-nearside-relay-site"),
        {"lunar-ground-gs-nearside-relay-site"},
    ) == ("lunar-ground-gs-nearside-relay-site", "luna-sat-p01s00")
    assert _checkpoint_ground_sat_pair(("gs-denver", "sat-p00s00"), {"gs-denver"}) == (
        "gs-denver",
        "sat-p00s00",
    )


@pytest.mark.parametrize(
    "pair",
    [
        ("sat-a", "sat-b"),
        ("gs-a", "gs-b"),
    ],
)
def test_checkpoint_pair_roles_reject_non_ground_or_double_ground_pairs(pair):
    from ome.main import _checkpoint_ground_sat_pair

    with pytest.raises(ValueError, match="expected exactly one ground station"):
        _checkpoint_ground_sat_pair(pair, {"gs-a", "gs-b"})


class TestExactEpochAnchor:
    """The checkpoint carries epoch_unix bit-exactly.

    sim_time is microsecond-quantized by datetime, so reconstructing the
    epoch from it loses sub-microsecond precision — and recovery replay
    must recompute history from bit-identical float inputs or replayed
    decisions can diverge from what was published.
    """

    def test_epoch_unix_survives_serialization_bit_exactly(self):
        fractional_epoch = 1_780_876_800.123456789  # not representable in µs
        ckpt = _checkpoint(epoch_unix=fractional_epoch)
        decoded = SchedulingCheckpoint.model_validate_json(ckpt.model_dump_json())
        assert decoded.epoch_unix == fractional_epoch

    def test_retained_wire_format_round_trips_through_the_codec_twins(self):
        # The gzip wire format is owned by encode/decode in ONE module;
        # the OME publisher thread encodes with the twin (serialization
        # moved off the pacing thread), so this round trip IS the wire
        # contract — bit-exact anchor included.
        from nodalarc.scheduling_checkpoint import (
            decode_retained_scheduling_checkpoint,
            encode_retained_scheduling_checkpoint,
        )

        fractional_epoch = 1_780_876_800.123456789
        ckpt = _checkpoint(epoch_unix=fractional_epoch)
        decoded = decode_retained_scheduling_checkpoint(encode_retained_scheduling_checkpoint(ckpt))
        assert decoded is not None
        assert decoded == ckpt
        assert decoded.epoch_unix == fractional_epoch
