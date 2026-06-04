# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for SchedulingCheckpoint model serialization and edge cases."""

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import CheckpointAssociation, SchedulingCheckpoint, TeardownEntry
from nodalarc.scheduling_checkpoint import decode_retained_scheduling_checkpoint


def _teardown(
    remaining_ticks: int,
    gs_id: str,
    sat_id: str,
    *,
    start_step: int = 40,
    successor: tuple[str, str] = ("gs-london", "sat-001"),
) -> TeardownEntry:
    return TeardownEntry(
        start_step=start_step,
        remaining_ticks=remaining_ticks,
        gs_id=gs_id,
        sat_id=sat_id,
        successor_node_a=successor[0],
        successor_node_b=successor[1],
    )


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


def _association(
    gs_id: str,
    sat_id: str,
    gs_terminal_index: int,
    sat_terminal_index: int,
) -> CheckpointAssociation:
    return CheckpointAssociation(
        gs_id=gs_id,
        sat_id=sat_id,
        gs_terminal_index=gs_terminal_index,
        sat_terminal_index=sat_terminal_index,
    )


def test_checkpoint_serialization_roundtrip():
    """Create checkpoint, serialize to JSON, deserialize, verify identical."""
    ckpt = _checkpoint(
        sim_time=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        epoch_id=3,
        snapshot_seq=99,
        step=42,
        associations={
            "gs-london:sat-001": _association("gs-london", "sat-001", 0, 1),
            "gs-tokyo:sat-047": _association("gs-tokyo", "sat-047", 1, 0),
        },
        pending_teardowns={
            "gs-london:sat-099": _teardown(2, "gs-london", "sat-099"),
        },
        paused=True,
        time_accel=25.0,
        written_at=1_750_000_000.0,
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert restored.sim_time == ckpt.sim_time
    assert restored.epoch_id == 3
    assert restored.snapshot_seq == 99
    assert restored.step == 42
    assert restored.associations["gs-london:sat-001"].gs_terminal_index == 0
    assert restored.associations["gs-london:sat-001"].sat_terminal_index == 1
    assert restored.associations["gs-tokyo:sat-047"].gs_id == "gs-tokyo"
    assert restored.paused is True
    assert restored.time_accel == 25.0
    assert restored.written_at == 1_750_000_000.0
    assert len(restored.pending_teardowns) == 1
    td = restored.pending_teardowns["gs-london:sat-099"]
    assert td.start_step == 40
    assert td.remaining_ticks == 2
    assert td.gs_id == "gs-london"
    assert td.sat_id == "sat-099"
    assert (td.successor_node_a, td.successor_node_b) == ("gs-london", "sat-001")


def test_checkpoint_with_empty_associations():
    """Empty associations (fresh session, no GS connected yet)."""
    ckpt = _checkpoint(
        sim_time=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        epoch_id=0,
        snapshot_seq=1,
        step=0,
        associations={},
        pending_teardowns={},
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert restored.associations == {}
    assert restored.pending_teardowns == {}
    assert restored.epoch_id == 0
    assert restored.snapshot_seq == 1
    assert restored.step == 0


def test_checkpoint_with_teardowns():
    """Verify TeardownEntry fields survive serialization."""
    teardowns = {
        "gs-nyc:sat-010": _teardown(
            5, "gs-nyc", "sat-010", start_step=1495, successor=("gs-nyc", "sat-011")
        ),
        "gs-paris:sat-020": _teardown(
            1, "gs-paris", "sat-020", start_step=1499, successor=("gs-paris", "sat-021")
        ),
        "gs-sydney:sat-030": _teardown(
            0, "gs-sydney", "sat-030", start_step=1500, successor=("gs-sydney", "sat-031")
        ),
    }

    ckpt = _checkpoint(
        sim_time=datetime(2025, 3, 15, 8, 30, 0, tzinfo=UTC),
        epoch_id=7,
        snapshot_seq=1234,
        step=1500,
        associations={
            "gs-nyc:sat-011": _association("gs-nyc", "sat-011", 0, 0),
            "gs-paris:sat-021": _association("gs-paris", "sat-021", 1, 0),
        },
        pending_teardowns=teardowns,
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert len(restored.pending_teardowns) == 3
    for key, entry in teardowns.items():
        restored_entry = restored.pending_teardowns[key]
        assert restored_entry.start_step == entry.start_step
        assert restored_entry.remaining_ticks == entry.remaining_ticks
        assert restored_entry.gs_id == entry.gs_id
        assert restored_entry.sat_id == entry.sat_id
        assert restored_entry.successor_node_a == entry.successor_node_a
        assert restored_entry.successor_node_b == entry.successor_node_b

    with pytest.raises(Exception, match="frozen"):
        restored.step = 999


def test_teardown_entry_frozen():
    """TeardownEntry is frozen — mutation raises."""
    entry = _teardown(3, "gs-a", "sat-b")
    with pytest.raises(Exception, match="frozen"):
        entry.remaining_ticks = 0


def test_checkpoint_associations_preserve_parallel_mbb_links():
    """MBB overlap can have multiple GS links; checkpoint keys by pair."""
    ckpt = _checkpoint(
        associations={
            "gs-london:sat-old": _association("gs-london", "sat-old", 0, 0),
            "gs-london:sat-new": _association("gs-london", "sat-new", 1, 0),
        }
    )

    restored = SchedulingCheckpoint.model_validate_json(ckpt.model_dump_json())

    assert set(restored.associations) == {"gs-london:sat-old", "gs-london:sat-new"}
    assert restored.associations["gs-london:sat-old"].gs_terminal_index == 0
    assert restored.associations["gs-london:sat-new"].gs_terminal_index == 1


def test_checkpoint_frozen():
    """SchedulingCheckpoint is frozen — mutation raises."""
    ckpt = _checkpoint()
    with pytest.raises(Exception, match="frozen"):
        ckpt.epoch_id = 99


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
