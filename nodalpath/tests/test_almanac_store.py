"""Tests for nodalpath.orchestrator.almanac_store."""

from __future__ import annotations

import json
from pathlib import Path

from nodalpath.models.almanac import AlmanacEntry, ForwardingTable, IngressRule
from nodalpath.orchestrator.almanac_store import AlmanacStore


def _make_entry(sim_time: str, node_ids: list[str] | None = None) -> AlmanacEntry:
    """Create a minimal AlmanacEntry for testing."""
    if node_ids is None:
        node_ids = ["sat-P00S00", "gs-alpha"]
    tables = []
    for nid in node_ids:
        ler_rules = []
        if nid.startswith("gs-"):
            ler_rules.append(
                IngressRule(
                    dst_prefix="172.16.1.0/24",
                    push_label=16001,
                    out_interface="gnd0",
                )
            )
        tables.append(
            ForwardingTable(
                node_id=nid,
                topology_state_id=f"ts-{sim_time}",
                sim_time=sim_time,
                lsr_bindings=[],
                ler_ingress_rules=ler_rules,
            )
        )
    return AlmanacEntry(
        topology_state_id=f"ts-{sim_time}",
        sim_time=sim_time,
        forwarding_tables=tables,
        computed_paths=["gs-alpha->gs-beta"],
        computation_time_ms=1.5,
    )


class TestAlmanacStore:
    """Tests for AlmanacStore."""

    def test_store_and_retrieve(self) -> None:
        """Stored entry can be retrieved by exact sim_time."""
        store = AlmanacStore()
        entry = _make_entry("2026-03-01T14:30:00+00:00")
        store.store(entry)
        result = store.get_entry_at("2026-03-01T14:30:00+00:00")
        assert result is not None
        assert result.sim_time == entry.sim_time

    def test_bisect_retrieval(self) -> None:
        """get_entry_at returns most recent entry <= query time."""
        store = AlmanacStore()
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        store.store(_make_entry("2026-03-01T14:30:30+00:00"))
        store.store(_make_entry("2026-03-01T14:31:00+00:00"))

        # Query between second and third entry
        result = store.get_entry_at("2026-03-01T14:30:45+00:00")
        assert result is not None
        assert result.sim_time == "2026-03-01T14:30:30+00:00"

    def test_bisect_before_all_entries(self) -> None:
        """get_entry_at returns None if query is before all entries."""
        store = AlmanacStore()
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        result = store.get_entry_at("2026-03-01T14:29:00+00:00")
        assert result is None

    def test_get_forwarding_table_existing(self) -> None:
        """get_forwarding_table returns the table for a specific node."""
        store = AlmanacStore()
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        ft = store.get_forwarding_table("2026-03-01T14:30:00+00:00", "sat-P00S00")
        assert ft is not None
        assert ft.node_id == "sat-P00S00"

    def test_get_forwarding_table_nonexistent_node(self) -> None:
        """get_forwarding_table returns None for a node not in the entry."""
        store = AlmanacStore()
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        ft = store.get_forwarding_table("2026-03-01T14:30:00+00:00", "sat-P99S99")
        assert ft is None

    def test_get_forwarding_table_no_entry(self) -> None:
        """get_forwarding_table returns None if no entry exists."""
        store = AlmanacStore()
        ft = store.get_forwarding_table("2026-03-01T14:30:00+00:00", "sat-P00S00")
        assert ft is None

    def test_jsonl_output(self, tmp_path: Path) -> None:
        """JSONL file is written on each store() call."""
        out = tmp_path / "almanac.jsonl"
        store = AlmanacStore(output_path=out)
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        store.store(_make_entry("2026-03-01T14:30:30+00:00"))

        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "topology_state_id" in parsed

    def test_jsonl_roundtrip(self, tmp_path: Path) -> None:
        """Entries written to JSONL can be parsed back to AlmanacEntry."""
        out = tmp_path / "almanac.jsonl"
        store = AlmanacStore(output_path=out)
        original = _make_entry("2026-03-01T14:30:00+00:00")
        store.store(original)

        line = out.read_text().strip()
        restored = AlmanacEntry.model_validate_json(line)
        assert restored.sim_time == original.sim_time
        assert len(restored.forwarding_tables) == len(original.forwarding_tables)

    def test_entry_count(self) -> None:
        """entry_count reflects the number of stored entries."""
        store = AlmanacStore()
        assert store.entry_count == 0
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        assert store.entry_count == 1
        store.store(_make_entry("2026-03-01T14:30:30+00:00"))
        assert store.entry_count == 2

    def test_transition_times(self) -> None:
        """transition_times returns sim_times in insertion order."""
        store = AlmanacStore()
        store.store(_make_entry("2026-03-01T14:30:00+00:00"))
        store.store(_make_entry("2026-03-01T14:30:30+00:00"))
        store.store(_make_entry("2026-03-01T14:31:00+00:00"))
        times = store.transition_times
        assert len(times) == 3
        assert times == sorted(times)

    def test_empty_store(self) -> None:
        """Empty store returns None for all queries."""
        store = AlmanacStore()
        assert store.entry_count == 0
        assert store.entries == []
        assert store.transition_times == []
        assert store.get_entry_at("2026-03-01T14:30:00+00:00") is None
