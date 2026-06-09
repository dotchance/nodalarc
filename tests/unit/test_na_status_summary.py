# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMMARY_PATH = ROOT / "scripts" / "na_status_summary.py"

spec = importlib.util.spec_from_file_location("na_status_summary", SUMMARY_PATH)
assert spec is not None and spec.loader is not None
na_status_summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(na_status_summary)


def _sat(node_id: str, segment_id: str) -> dict:
    return {
        "node_id": node_id,
        "node_type": "satellite",
        "segment_id": segment_id,
    }


def _ground(node_id: str, segment_id: str) -> dict:
    return {
        "node_id": node_id,
        "node_type": "ground_station",
        "segment_id": segment_id,
    }


def _link(node_a: str, node_b: str, link_type: str, segments: tuple[str, str] | None) -> dict:
    link = {"node_a": node_a, "node_b": node_b, "link_type": link_type}
    if segments is not None:
        link["endpoint_segments"] = list(segments)
    return link


def test_status_summary_groups_nodes_and_isls_by_constellation() -> None:
    state = {
        "nodes": [
            _sat("leo-sat-0", "leo"),
            _sat("leo-sat-1", "leo"),
            _sat("meo-sat-0", "meo"),
            _sat("meo-sat-1", "meo"),
            _ground("ground-denver", "ground"),
        ],
        "links": [
            _link("leo-sat-0", "leo-sat-1", "isl", ("leo", "leo")),
            _link("meo-sat-0", "meo-sat-1", "isl", ("meo", "meo")),
            _link("leo-sat-1", "meo-sat-0", "inter_constellation", ("leo", "meo")),
            _link("ground-denver", "leo-sat-0", "ground", ("ground", "leo")),
        ],
    }

    summary = na_status_summary.summarize_state(state)

    assert "leo: 2 satellite nodes, 1 ISL" in summary
    assert "meo: 2 satellite nodes, 1 ISL" in summary
    assert "ground: 1 ground node" in summary
    assert "leo <-> meo: 1 link" in summary
    assert "ISLs: 2" in summary
    assert "Inter-constellation links: 1" in summary
    assert "Ground links: 1" in summary
    assert "Total active: 4" in summary


def test_status_summary_keeps_old_internal_isl_names_compatible() -> None:
    state = {
        "nodes": [
            _sat("leo-sat-0", "leo"),
            _sat("leo-sat-1", "leo"),
            _sat("leo-sat-2", "leo"),
        ],
        "links": [
            _link("leo-sat-0", "leo-sat-1", "intra_plane_isl", None),
            _link("leo-sat-1", "leo-sat-2", "cross_plane_isl", None),
        ],
    }

    summary = na_status_summary.summarize_state(state)

    assert "leo: 3 satellite nodes, 2 ISLs" in summary
    assert "ISLs: 2" in summary
    assert "Total active: 2" in summary
