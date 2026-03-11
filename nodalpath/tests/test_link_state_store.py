"""Tests for LinkStateStore storage, retrieval, and persistence."""

import json

from nodalpath.orchestrator.link_state_store import LinkStateStore


def _full_link_state(n_active=2, n_vis_unscheduled=1):
    state = {}
    for i in range(n_active):
        state[(f"sat-P0{i}S00", f"sat-P0{i}S01")] = (True, True, 1000.0)
    for i in range(n_vis_unscheduled):
        state[(f"sat-P0{i}S02", f"sat-P0{i}S03")] = (True, False, 800.0)
    return state


def test_store_and_get():
    store = LinkStateStore()
    store.store("s1", _full_link_state(), "2026-01-01T00:01:00Z")
    records = store.get("s1")
    assert records is not None
    assert len(records) == 3  # 2 active + 1 vis_unscheduled


def test_get_missing_returns_none():
    store = LinkStateStore()
    assert store.get("nonexistent") is None


def test_get_by_sim_time_exact():
    store = LinkStateStore()
    store.store("s1", _full_link_state(), "2026-01-01T00:01:00Z")
    records = store.get_by_sim_time("2026-01-01T00:01:00Z")
    assert records is not None


def test_get_by_sim_time_before_any_entry():
    store = LinkStateStore()
    store.store("s1", _full_link_state(), "2026-01-01T00:05:00Z")
    records = store.get_by_sim_time("2026-01-01T00:01:00Z")
    assert records is None


def test_get_by_sim_time_between_entries():
    store = LinkStateStore()
    store.store("s1", _full_link_state(1, 0), "2026-01-01T00:01:00Z")
    store.store("s2", _full_link_state(2, 0), "2026-01-01T00:05:00Z")
    # Query between the two — should return s1
    records = store.get_by_sim_time("2026-01-01T00:03:00Z")
    assert records is not None
    assert len(records) == 1


def test_future_entries_not_written_to_disk(tmp_path):
    path = tmp_path / "links.jsonl"
    store = LinkStateStore(output_path=path)
    store.store("s1", _full_link_state(), "2026-01-01T00:01:00Z", is_future=False)
    store.store("s2", _full_link_state(), "2026-01-01T02:00:00Z", is_future=True)
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["topology_state_id"] == "s1"


def test_load_from_jsonl(tmp_path):
    path = tmp_path / "links.jsonl"
    store1 = LinkStateStore(output_path=path)
    store1.store("s1", _full_link_state(2, 1), "2026-01-01T00:01:00Z")

    store2 = LinkStateStore()
    loaded = store2.load_from_jsonl(path)
    assert loaded == 1
    records = store2.get("s1")
    assert records is not None
    assert len(records) == 3
