# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Scheduler catalog-runtime seam tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.resolve_session import load_session_resolution_from_file, resolve_session
from nodalarc.session_identity import require_resolved_session_run_id
from scheduler.__main__ import (
    _dispatch_timing,
    _routing_protocol_label,
    _scheduler_terminal_maps,
)

from tests.catalog_session_fixtures import shipped_read_view

ROOT = Path(__file__).resolve().parents[3]
SESSION = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-heo-geo-luna-reachability.yaml"


def _resolved():
    return load_session_resolution_from_file(
        SESSION, origin="test.scheduler", run_id="run-test-0001", catalog=shipped_read_view()
    ).resolved


def test_scheduler_runtime_identity_comes_from_source_context_not_session_yaml(
    tmp_path: Path,
) -> None:
    resolution = load_session_resolution_from_file(
        SESSION, origin="test.scheduler", run_id="run-test-0001", catalog=shipped_read_view()
    )

    assert require_resolved_session_run_id(resolution.resolved) == "run-test-0001"
    assert "run_id" not in resolution.catalog_session.session.model_dump(mode="python")


def test_resolved_runtime_identity_fails_loud_without_operator_run_id() -> None:
    resolved = resolve_session(
        yaml.safe_load(SESSION.read_text(encoding="utf-8")), catalog=shipped_read_view()
    )

    with pytest.raises(ValueError, match="source_context.run_id"):
        require_resolved_session_run_id(resolved)


def test_scheduler_reads_terminal_capacity_and_stated_modes_from_the_resolved_session() -> None:
    resolved = _resolved()

    capacities, modes, tracked = _scheduler_terminal_maps(resolved)
    ground_candidates = resolved.ground_candidate_satellites_by_gs()
    candidate_nodes = set(ground_candidates) | {
        sat for sats in ground_candidates.values() for sat in sats
    }

    # Every node of an access candidate has terminals OME may assign, and the nodes that
    # state scheduling in this session are its stations.
    assert candidate_nodes.issubset(capacities)
    assert set(modes) == set(ground_candidates)
    # Each access pair has one node that states scheduling: its station chooses the link.
    # Neither node of a pair between orbits states scheduling, so both of them choose it.
    between_orbits = {
        node_id
        for candidate in resolved.link_candidates
        if candidate.assigned_terminals and candidate.kind != "access"
        for node_id in candidate.pair
    }
    assert between_orbits
    assert between_orbits.issubset(capacities)
    assert tracked == set(ground_candidates) | between_orbits
    assert all(capacity > 0 for capacity in capacities.values())


def test_scheduler_dispatch_timing_and_protocol_label_are_catalog_resolved() -> None:
    resolved = _resolved()

    assert _dispatch_timing(resolved) == (10.0, 1.0)
    assert _routing_protocol_label(resolved) == "isis"


def test_scheduler_dispatch_timing_requires_resolved_time_and_dispatch() -> None:
    resolved = _resolved()

    with pytest.raises(RuntimeError, match="dispatch"):
        _dispatch_timing(resolved.model_copy(update={"dispatch": None}))
    with pytest.raises(RuntimeError, match="time"):
        _dispatch_timing(resolved.model_copy(update={"time": None}))
