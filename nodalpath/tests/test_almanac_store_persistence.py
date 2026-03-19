"""Tests for AlmanacStore load_from_jsonl, is_future flag, and get_timeline_ticks."""

from __future__ import annotations

import json
from pathlib import Path

from nodalpath.models.almanac import AlmanacEntry, ForwardingTable, IngressRule
from nodalpath.orchestrator.almanac_store import AlmanacStore


def _make_entry(sim_time: str, state_id: str | None = None) -> AlmanacEntry:
    """Create a minimal AlmanacEntry for testing."""
    sid = state_id or f"ts-{sim_time}"
    return AlmanacEntry(
        topology_state_id=sid,
        sim_time=sim_time,
        forwarding_tables=[
            ForwardingTable(
                node_id="sat-P00S00",
                topology_state_id=sid,
                sim_time=sim_time,
                lsr_bindings=[],
                ler_ingress_rules=[],
            ),
            ForwardingTable(
                node_id="gs-alpha",
                topology_state_id=sid,
                sim_time=sim_time,
                lsr_bindings=[],
                ler_ingress_rules=[
                    IngressRule(
                        dst_prefix="172.16.1.0/24",
                        push_label=16001,
                        out_interface="gnd0",
                    ),
                ],
            ),
        ],
        computed_paths=["gs-alpha->gs-beta"],
        computation_time_ms=1.0,
    )


def test_load_from_jsonl_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "almanac.jsonl"
    path.write_text("")
    store = AlmanacStore()
    loaded = store.load_from_jsonl(path)
    assert loaded == 0


def test_load_from_jsonl_nonexistent(tmp_path: Path) -> None:
    store = AlmanacStore()
    loaded = store.load_from_jsonl(tmp_path / "missing.jsonl")
    assert loaded == 0


def test_load_from_jsonl_restores_entries(tmp_path: Path) -> None:
    path = tmp_path / "almanac.jsonl"
    store1 = AlmanacStore(output_path=path)
    entry = _make_entry("2026-01-01T00:01:00+00:00", "s1")
    store1.store(entry)

    store2 = AlmanacStore()
    loaded = store2.load_from_jsonl(path)
    assert loaded == 1
    assert store2.entry_count == 1
    assert store2.get_entry_at("2026-01-01T00:01:00+00:00").topology_state_id == "s1"


def test_future_entries_not_persisted(tmp_path: Path) -> None:
    path = tmp_path / "almanac.jsonl"
    store = AlmanacStore(output_path=path)

    past = _make_entry("2026-01-01T00:01:00+00:00", "s1")
    future = _make_entry("2026-01-01T02:00:00+00:00", "s2")
    future = future.model_copy(update={"is_future": True})

    store.store(past)
    store.store(future)

    # Read the JSONL back — should only have the past entry
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["topology_state_id"] == "s1"


def test_is_future_defaults_false_on_load(tmp_path: Path) -> None:
    path = tmp_path / "almanac.jsonl"
    store1 = AlmanacStore(output_path=path)
    store1.store(_make_entry("2026-01-01T00:01:00+00:00", "s1"))

    store2 = AlmanacStore()
    store2.load_from_jsonl(path)
    entry = store2.get_entry_at("2026-01-01T00:01:00+00:00")
    assert entry.is_future is False


def test_get_timeline_ticks_includes_future() -> None:
    store = AlmanacStore()
    past = _make_entry("2026-01-01T00:01:00+00:00", "s1")
    future = _make_entry("2026-01-01T02:00:00+00:00", "s2")
    future = future.model_copy(update={"is_future": True})
    store.store(past)
    store.store(future)
    ticks = store.get_timeline_ticks()
    assert len(ticks) == 2
    assert ticks[0]["is_future"] is False
    assert ticks[1]["is_future"] is True
