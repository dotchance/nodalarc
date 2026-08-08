# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Binding resolution against a real resolved session.

Every refusal here is the explicit-selection boundary: an invalid explicit
selection raises a typed error and never returns a substitute selection.
Selecting the complete built-in FRR profile when no binding is present
belongs to the runtime seam that consumes this component, and its
no-fallback proof becomes executable there.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.workloads.refs import ImplementationBindingRef
from nodalarc.workloads.resolution import (
    BindingResolutionCode,
    BindingResolutionError,
    ResolvedWorldInvariantError,
    WorkloadSelection,
    resolve_node_workloads,
)
from nodalarc.workloads.source import DirectoryPackageSource, LoadedPackage

from tests.catalog_session_fixtures import (
    build_catalog_session_fixture,
    resolve_catalog_session,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "workloads"

FRR = "nodalarc:profiles/frr/frr-reference.yaml"
ZERO = "nodalarc:profiles/zero-capability.yaml"
STATIC = "nodalarc:profiles/static-realizer.yaml"


@pytest.fixture(scope="module")
def resolved() -> ResolvedSession:
    fixture = build_catalog_session_fixture(
        name="workload-binding",
        constellation={},
        ground_stations={"stations": [{}, {}]},
    )
    return resolve_catalog_session(fixture)


def _package(tmp_path: Path, entries: list[dict[str, Any]]) -> LoadedPackage:
    """Author a binding over the fixture profiles and load it for real."""
    root = tmp_path / "package"
    shutil.copytree(FIXTURES / "profiles", root / "profiles")
    (root / "bindings").mkdir()
    document = {
        "implementation_binding": {
            "schema_version": "1",
            "id": "under-test",
            "description": "Authored for one resolution scenario.",
            "entries": entries,
        }
    }
    (root / "bindings" / "under-test.yaml").write_text(yaml.safe_dump(document))
    return DirectoryPackageSource(root).load(
        ImplementationBindingRef("nodalarc:bindings/under-test.yaml")
    )


def _satellites(resolved: ResolvedSession) -> list[str]:
    return sorted(n.node_id for n in resolved.nodes if n.kind == "satellite")


def _grounds(resolved: ResolvedSession) -> list[str]:
    return sorted(n.node_id for n in resolved.nodes if n.kind == "ground_station")


def test_remainder_maps_every_node_to_exactly_one_profile(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    package = _package(
        tmp_path, [{"id": "everything", "selector": {"remainder": True}, "profile": FRR}]
    )
    selection = resolve_node_workloads(resolved, package)
    assert isinstance(selection, WorkloadSelection)
    assert selection.binding_ref == package.binding_ref
    assert selection.package_digest == package.package_digest
    assigned = sorted(assignment.node_id for assignment in selection.assignments)
    assert assigned == sorted(node.node_id for node in resolved.nodes)
    assert len(set(assigned)) == len(assigned)
    assert {a.profile_ref for a in selection.assignments} == {FRR}


@pytest.mark.parametrize("selector", [{"forwarding": "routed"}, {"domain": "test_domain"}])
def test_single_full_cover_selectors_assign_every_node(
    resolved: ResolvedSession, tmp_path: Path, selector: dict[str, Any]
) -> None:
    package = _package(tmp_path, [{"id": "all", "selector": selector, "profile": FRR}])
    selection = resolve_node_workloads(resolved, package)
    assert len(selection.assignments) == len(resolved.nodes)
    assert {a.entry_id for a in selection.assignments} == {"all"}


def test_kind_selectors_partition_with_entry_attribution(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    package = _package(
        tmp_path,
        [
            {"id": "space", "selector": {"node_kind": "satellite"}, "profile": FRR},
            {"id": "ground", "selector": {"node_kind": "ground_station"}, "profile": FRR},
        ],
    )
    selection = resolve_node_workloads(resolved, package)
    by_entry: dict[str, list[str]] = {}
    for assignment in selection.assignments:
        by_entry.setdefault(assignment.entry_id, []).append(assignment.node_id)
    assert sorted(by_entry["space"]) == _satellites(resolved)
    assert sorted(by_entry["ground"]) == _grounds(resolved)


def test_explicit_nodes_take_their_entry_over_the_remainder(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    chosen = _satellites(resolved)[:2]
    package = _package(
        tmp_path,
        [
            {"id": "chosen", "selector": {"nodes": chosen}, "profile": FRR},
            {"id": "rest", "selector": {"remainder": True}, "profile": FRR},
        ],
    )
    selection = resolve_node_workloads(resolved, package)
    for assignment in selection.assignments:
        expected = "chosen" if assignment.node_id in chosen else "rest"
        assert assignment.entry_id == expected


def test_assignments_are_neutral_to_authored_entry_order(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    chosen = _satellites(resolved)[:1]
    entries = [
        {"id": "chosen", "selector": {"nodes": chosen}, "profile": FRR},
        {"id": "rest", "selector": {"remainder": True}, "profile": FRR},
    ]
    forward = resolve_node_workloads(resolved, _package(tmp_path / "a", entries))
    reversed_order = resolve_node_workloads(
        resolved, _package(tmp_path / "b", list(reversed(entries)))
    )
    assert forward.assignments == reversed_order.assignments


def test_unknown_explicit_node_is_a_typed_refusal(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    package = _package(
        tmp_path,
        [
            {"id": "ghost", "selector": {"nodes": ["no-such-node"]}, "profile": FRR},
            {"id": "rest", "selector": {"remainder": True}, "profile": FRR},
        ],
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_SELECTOR_UNKNOWN_NODE
    example = excinfo.value.evidence.examples[0]
    assert example.node_id == "no-such-node"
    assert example.entry_ids == ("ghost",)


def test_empty_selector_is_a_typed_refusal(resolved: ResolvedSession, tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        [
            {"id": "nothing", "selector": {"tag": "no-such-tag"}, "profile": FRR},
            {"id": "rest", "selector": {"remainder": True}, "profile": FRR},
        ],
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_SELECTOR_EMPTY


def test_overlapping_entries_are_a_typed_refusal(resolved: ResolvedSession, tmp_path: Path) -> None:
    satellites = _satellites(resolved)
    segment = next(node.segment_id for node in resolved.nodes if node.kind == "satellite")
    package = _package(
        tmp_path,
        [
            {"id": "by-segment", "selector": {"segment": segment}, "profile": FRR},
            {"id": "by-kind", "selector": {"node_kind": "satellite"}, "profile": FRR},
            {"id": "rest", "selector": {"remainder": True}, "profile": FRR},
        ],
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_NODE_OVERLAP
    overlapped = {example.node_id for example in excinfo.value.evidence.examples}
    assert overlapped == set(satellites)


def test_uncovered_nodes_are_a_typed_refusal(resolved: ResolvedSession, tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        [{"id": "space-only", "selector": {"node_kind": "satellite"}, "profile": FRR}],
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_NODE_UNMATCHED
    unmatched = {example.node_id for example in excinfo.value.evidence.examples}
    assert unmatched == set(_grounds(resolved))


def test_realization_mismatch_is_a_typed_refusal(resolved: ResolvedSession, tmp_path: Path) -> None:
    package = _package(
        tmp_path, [{"id": "everything", "selector": {"remainder": True}, "profile": ZERO}]
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_REALIZATION_MISMATCH
    example = excinfo.value.evidence.examples[0]
    assert example.profile_ref == ZERO
    assert example.domain_id == "test_domain"


def test_static_realizer_cannot_serve_an_isis_domain(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    package = _package(
        tmp_path, [{"id": "everything", "selector": {"remainder": True}, "profile": STATIC}]
    )
    with pytest.raises(BindingResolutionError) as excinfo:
        resolve_node_workloads(resolved, package)
    assert excinfo.value.code == BindingResolutionCode.BINDING_REALIZATION_MISMATCH


def test_broken_resolved_world_is_a_platform_error_not_a_refusal(
    resolved: ResolvedSession, tmp_path: Path
) -> None:
    package = _package(
        tmp_path, [{"id": "everything", "selector": {"remainder": True}, "profile": FRR}]
    )
    first = resolved.nodes[0]
    broken = resolved.model_copy(
        update={"nodes": (first.model_copy(update={"forwarding": None}),) + resolved.nodes[1:]}
    )
    with pytest.raises(ResolvedWorldInvariantError):
        resolve_node_workloads(broken, package)
