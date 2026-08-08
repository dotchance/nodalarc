# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Pure binding resolution: one workload profile per resolved node.

Evaluates a loaded package against one immutable resolved session. Match sets
are computed first, in canonical entry-ID order over a selector index built
once, and refusals then apply in one fixed precedence, so the same invalid
binding produces identical evidence regardless of input ordering. A broken
resolved world raises its own invariant exception, never a semantic refusal,
and an invalid selection raises a typed refusal, never a substitute
selection.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.content_identity import Sha256Digest
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.workloads.refs import ImplementationBindingRef, ProfileRef
from nodalarc.workloads.source import LoadedPackage

_EVIDENCE_EXAMPLE_LIMIT: Final = 16


class ResolvedWorldInvariantError(RuntimeError):
    """The resolved session violates its own invariants.

    Deliberately not a BindingResolutionError: a broken platform artifact is
    a platform failure, never a refusal of the user's selection.
    """


class BindingResolutionCode(StrEnum):
    BINDING_SELECTOR_UNKNOWN_NODE = "BINDING_SELECTOR_UNKNOWN_NODE"
    BINDING_SELECTOR_EMPTY = "BINDING_SELECTOR_EMPTY"
    BINDING_NODE_OVERLAP = "BINDING_NODE_OVERLAP"
    BINDING_NODE_UNMATCHED = "BINDING_NODE_UNMATCHED"
    BINDING_REALIZATION_MISMATCH = "BINDING_REALIZATION_MISMATCH"


class BindingResolutionExample(BaseModel):
    """One structured refusal example preserving node/entry relationships."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    node_id: str | None = None
    entry_ids: tuple[str, ...] = ()
    profile_ref: ProfileRef | None = None
    domain_id: str | None = None


class BindingResolutionEvidence(BaseModel):
    """Deterministic, bounded refusal evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: BindingResolutionCode
    object_ref: ImplementationBindingRef
    detail: str = Field(min_length=1, max_length=512)
    total: int = Field(ge=1)
    examples: tuple[BindingResolutionExample, ...] = Field(max_length=_EVIDENCE_EXAMPLE_LIMIT)
    omitted_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> BindingResolutionEvidence:
        if self.omitted_count != self.total - len(self.examples):
            raise ValueError("omitted_count must equal total minus the example count")
        return self


class BindingResolutionError(ValueError):
    """Typed semantic refusal; an invalid selection never yields a result."""

    def __init__(self, evidence: BindingResolutionEvidence) -> None:
        super().__init__(evidence.detail)
        self.evidence = evidence

    @property
    def code(self) -> BindingResolutionCode:
        return self.evidence.code


def _refuse(
    code: BindingResolutionCode,
    object_ref: ImplementationBindingRef,
    detail: str,
    examples: list[BindingResolutionExample],
) -> BindingResolutionError:
    return BindingResolutionError(
        BindingResolutionEvidence(
            code=code,
            object_ref=object_ref,
            detail=detail,
            total=len(examples),
            examples=tuple(examples[:_EVIDENCE_EXAMPLE_LIMIT]),
            omitted_count=max(0, len(examples) - _EVIDENCE_EXAMPLE_LIMIT),
        )
    )


class NodeAssignment(BaseModel):
    """One resolved node bound to one workload profile."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    node_id: str
    # Diagnostic only: names the binding entry that selected the node.
    entry_id: str
    profile_ref: ProfileRef


class WorkloadSelection(BaseModel):
    """The desired selection: every node assigned, one package identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    binding_ref: ImplementationBindingRef
    package_digest: Sha256Digest
    assignments: tuple[NodeAssignment, ...]


class _WorldIndex:
    """One-pass selector index over the resolved world, validated as built."""

    def __init__(self, resolved: ResolvedSession) -> None:
        self.universe: set[str] = set()
        self.by_segment: dict[str, set[str]] = {}
        self.by_kind: dict[str, set[str]] = {}
        self.by_forwarding: dict[str, set[str]] = {}
        self.by_domain: dict[str, set[str]] = {}
        self.by_tag: dict[str, set[str]] = {}
        self.forwarding: dict[str, str] = {}
        # node id -> (domain id, protocol, capabilities); at most one per node.
        self.domain_of: dict[str, tuple[str, str, frozenset[str]]] = {}

        for node in resolved.nodes:
            node_id = node.node_id
            if node.forwarding is None:
                raise ResolvedWorldInvariantError(
                    f"resolved node {node_id} has no forwarding class"
                )
            self.universe.add(node_id)
            self.forwarding[node_id] = node.forwarding
            self.by_kind.setdefault(node.kind, set()).add(node_id)
            self.by_forwarding.setdefault(node.forwarding, set()).add(node_id)
            # Ground nodes match their segment or any placement group;
            # satellites and relays match only their segment.
            segments = {node.segment_id}
            if node.kind == "ground_station":
                segments.update(node.placement_groups)
            for segment in segments:
                self.by_segment.setdefault(segment, set()).add(node_id)
            for tag in node.tags:
                self.by_tag.setdefault(tag, set()).add(node_id)

        for domain in resolved.routing_domains:
            for node_id in domain.node_ids:
                if node_id not in self.universe:
                    raise ResolvedWorldInvariantError(
                        f"routing domain {domain.domain_id} names unknown node {node_id}"
                    )
                if node_id in self.domain_of:
                    raise ResolvedWorldInvariantError(
                        f"resolved node {node_id} belongs to more than one routing domain"
                    )
                self.domain_of[node_id] = (
                    domain.domain_id,
                    domain.protocol,
                    frozenset(domain.capabilities),
                )
                self.by_domain.setdefault(domain.domain_id, set()).add(node_id)

        for node_id, forwarding in self.forwarding.items():
            if forwarding == "routed" and node_id not in self.domain_of:
                raise ResolvedWorldInvariantError(
                    f"routed node {node_id} belongs to no routing domain"
                )


def resolve_node_workloads(
    resolved: ResolvedSession,
    package: LoadedPackage,
) -> WorkloadSelection:
    """Resolve one loaded binding package against one resolved session."""

    index = _WorldIndex(resolved)
    object_ref = package.binding_ref

    # First pass: compute every match set in canonical entry-ID order.
    entries = sorted(package.binding.entries, key=lambda entry: entry.id)
    matched: dict[str, set[str]] = {}
    unknown_examples: list[BindingResolutionExample] = []
    empty_entry_ids: list[str] = []
    remainder_id: str | None = None
    for entry in entries:
        selector = entry.selector
        if selector.remainder is not None:
            remainder_id = entry.id
            continue
        if selector.nodes is not None:
            unknown = sorted(set(selector.nodes).difference(index.universe))
            unknown_examples.extend(
                BindingResolutionExample(node_id=node_id, entry_ids=(entry.id,))
                for node_id in unknown
            )
            selected = set(selector.nodes).intersection(index.universe)
        elif selector.segment is not None:
            selected = index.by_segment.get(selector.segment, set())
        elif selector.node_kind is not None:
            selected = index.by_kind.get(selector.node_kind, set())
        elif selector.forwarding is not None:
            selected = index.by_forwarding.get(selector.forwarding, set())
        elif selector.domain is not None:
            selected = index.by_domain.get(selector.domain, set())
        else:
            assert selector.tag is not None
            selected = index.by_tag.get(selector.tag, set())
        matched[entry.id] = set(selected)
        if not selected:
            empty_entry_ids.append(entry.id)

    matched_union: set[str] = set().union(*matched.values()) if matched else set()
    unmatched = sorted(index.universe.difference(matched_union))
    if remainder_id is not None:
        if unmatched:
            matched[remainder_id] = set(unmatched)
        else:
            empty_entry_ids.append(remainder_id)
        unmatched = []

    # Second pass: one fixed refusal precedence over the complete match sets.
    if unknown_examples:
        raise _refuse(
            BindingResolutionCode.BINDING_SELECTOR_UNKNOWN_NODE,
            object_ref,
            "Binding selectors name nodes absent from the resolved session",
            unknown_examples,
        )
    if empty_entry_ids:
        raise _refuse(
            BindingResolutionCode.BINDING_SELECTOR_EMPTY,
            object_ref,
            "Binding entries match no resolved node",
            [
                BindingResolutionExample(entry_ids=(entry_id,))
                for entry_id in sorted(empty_entry_ids)
            ],
        )
    owners: dict[str, list[str]] = {}
    for entry_id in sorted(matched):
        for node_id in matched[entry_id]:
            owners.setdefault(node_id, []).append(entry_id)
    overlap_examples = [
        BindingResolutionExample(node_id=node_id, entry_ids=tuple(entry_ids))
        for node_id, entry_ids in sorted(owners.items())
        if len(entry_ids) > 1
    ]
    if overlap_examples:
        raise _refuse(
            BindingResolutionCode.BINDING_NODE_OVERLAP,
            object_ref,
            "Binding entries overlap on resolved nodes",
            overlap_examples,
        )
    if unmatched:
        raise _refuse(
            BindingResolutionCode.BINDING_NODE_UNMATCHED,
            object_ref,
            "Binding leaves resolved nodes without a workload profile",
            [BindingResolutionExample(node_id=node_id) for node_id in unmatched],
        )

    entry_by_id = {entry.id: entry for entry in entries}
    mismatch_examples: list[BindingResolutionExample] = []
    for node_id in sorted(owners):
        (entry_id,) = owners[node_id]
        loaded = package.profiles[str(entry_by_id[entry_id].profile)]
        if index.forwarding[node_id] != "routed":
            # Realization on non-routed nodes is compatible-or-unused,
            # never demanded.
            continue
        domain_id, protocol, capabilities = index.domain_of[node_id]
        realizes = loaded.profile.routing_realization.realizes or ()
        compatible = [
            realization
            for realization in realizes
            if realization.protocol == protocol
            and capabilities.issubset(set(realization.capabilities))
        ]
        if len(compatible) != 1:
            mismatch_examples.append(
                BindingResolutionExample(
                    node_id=node_id,
                    entry_ids=(entry_id,),
                    profile_ref=loaded.ref,
                    domain_id=domain_id,
                )
            )
    if mismatch_examples:
        raise _refuse(
            BindingResolutionCode.BINDING_REALIZATION_MISMATCH,
            object_ref,
            "Selected profiles do not compatibly realize their routed nodes' domains",
            mismatch_examples,
        )

    assignments = tuple(
        NodeAssignment(
            node_id=node_id,
            entry_id=owners[node_id][0],
            profile_ref=entry_by_id[owners[node_id][0]].profile,
        )
        for node_id in sorted(owners)
    )
    return WorkloadSelection(
        binding_ref=object_ref,
        package_digest=package.package_digest,
        assignments=assignments,
    )
