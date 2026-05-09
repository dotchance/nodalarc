# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit tests for SchedulingCheckpoint model serialization and edge cases."""

from datetime import UTC, datetime

from nodalarc.models.events import SchedulingCheckpoint, TeardownEntry


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


def test_checkpoint_serialization_roundtrip():
    """Create checkpoint, serialize to JSON, deserialize, verify identical."""
    ckpt = _checkpoint(
        sim_time=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        epoch_id=3,
        snapshot_seq=99,
        step=42,
        associations={"gs-london": "sat-001", "gs-tokyo": "sat-047"},
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
    assert restored.associations == {"gs-london": "sat-001", "gs-tokyo": "sat-047"}
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
        associations={"gs-nyc": "sat-011", "gs-paris": "sat-021"},
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

    # Verify frozen (immutable)
    try:
        restored.step = 999
        assert False, "Should have raised"
    except Exception:
        pass  # Expected — frozen model


def test_teardown_entry_frozen():
    """TeardownEntry is frozen — mutation raises."""
    entry = _teardown(3, "gs-a", "sat-b")
    try:
        entry.remaining_ticks = 0
        assert False, "Should have raised"
    except Exception:
        pass


def test_checkpoint_frozen():
    """SchedulingCheckpoint is frozen — mutation raises."""
    ckpt = _checkpoint()
    try:
        ckpt.epoch_id = 99
        assert False, "Should have raised"
    except Exception:
        pass
