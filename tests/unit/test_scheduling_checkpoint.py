# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit tests for SchedulingCheckpoint model serialization and edge cases."""

from datetime import UTC, datetime

from nodalarc.models.events import SchedulingCheckpoint, TeardownEntry


def test_checkpoint_serialization_roundtrip():
    """Create checkpoint, serialize to JSON, deserialize, verify identical."""
    ckpt = SchedulingCheckpoint(
        sim_time=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        epoch_id=3,
        step=42,
        associations={"gs-london": "sat-001", "gs-tokyo": "sat-047"},
        pending_teardowns={
            "gs-london:sat-099": TeardownEntry(
                remaining_ticks=2, gs_id="gs-london", sat_id="sat-099"
            ),
        },
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert restored.sim_time == ckpt.sim_time
    assert restored.epoch_id == 3
    assert restored.step == 42
    assert restored.associations == {"gs-london": "sat-001", "gs-tokyo": "sat-047"}
    assert len(restored.pending_teardowns) == 1
    td = restored.pending_teardowns["gs-london:sat-099"]
    assert td.remaining_ticks == 2
    assert td.gs_id == "gs-london"
    assert td.sat_id == "sat-099"


def test_checkpoint_with_empty_associations():
    """Empty associations (fresh session, no GS connected yet)."""
    ckpt = SchedulingCheckpoint(
        sim_time=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        epoch_id=0,
        step=0,
        associations={},
        pending_teardowns={},
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert restored.associations == {}
    assert restored.pending_teardowns == {}
    assert restored.epoch_id == 0
    assert restored.step == 0


def test_checkpoint_with_teardowns():
    """Verify TeardownEntry fields survive serialization."""
    teardowns = {
        "gs-nyc:sat-010": TeardownEntry(remaining_ticks=5, gs_id="gs-nyc", sat_id="sat-010"),
        "gs-paris:sat-020": TeardownEntry(remaining_ticks=1, gs_id="gs-paris", sat_id="sat-020"),
        "gs-sydney:sat-030": TeardownEntry(remaining_ticks=0, gs_id="gs-sydney", sat_id="sat-030"),
    }

    ckpt = SchedulingCheckpoint(
        sim_time=datetime(2025, 3, 15, 8, 30, 0, tzinfo=UTC),
        epoch_id=7,
        step=1500,
        associations={"gs-nyc": "sat-011", "gs-paris": "sat-021"},
        pending_teardowns=teardowns,
    )

    json_bytes = ckpt.model_dump_json().encode()
    restored = SchedulingCheckpoint.model_validate_json(json_bytes)

    assert len(restored.pending_teardowns) == 3
    for key, entry in teardowns.items():
        restored_entry = restored.pending_teardowns[key]
        assert restored_entry.remaining_ticks == entry.remaining_ticks
        assert restored_entry.gs_id == entry.gs_id
        assert restored_entry.sat_id == entry.sat_id

    # Verify frozen (immutable)
    try:
        restored.step = 999
        assert False, "Should have raised"
    except Exception:
        pass  # Expected — frozen model


def test_teardown_entry_frozen():
    """TeardownEntry is frozen — mutation raises."""
    entry = TeardownEntry(remaining_ticks=3, gs_id="gs-a", sat_id="sat-b")
    try:
        entry.remaining_ticks = 0
        assert False, "Should have raised"
    except Exception:
        pass


def test_checkpoint_frozen():
    """SchedulingCheckpoint is frozen — mutation raises."""
    ckpt = SchedulingCheckpoint(
        sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        epoch_id=0,
        step=0,
        associations={},
        pending_teardowns={},
    )
    try:
        ckpt.epoch_id = 99
        assert False, "Should have raised"
    except Exception:
        pass
