# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Link allocation engine.

This module owns the OME allocation role: given the candidate pairs physics found usable at a
tick and the prior scheduling state, it chooses which pairs are linked and on which terminals,
and gives every other usable pair a typed reason. It does not propagate or evaluate orbital
physics, and it does not publish NATS events.

The allocator knows nodes, named terminal pools, caps, held links, handovers in progress and
the session's rank order. It does not know what a ground station or a satellite is. Who chooses
a pair is read from the configuration: the nodes the caller names as stating scheduling. A node
that states scheduling chooses among its candidates by its selection policy and gives up a held
link by its handover policy and mode. The other node of such a pair does not choose: contention
for its terminals is resolved in the session's rank order, and a link it gives up is released at
once.

Both nodes of a pair run one routine (``check_end``). The two cases differ only in what they
pass to it: the score the displacement rule compares, and how the held link ends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal, NamedTuple

from nodalarc.models.ground_policy import (
    CrossTenantDisplacementPolicy,
    HandoverPolicySpec,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicySpec,
    SuccessorAbortPolicy,
)
from nodalarc.models.link_decisions import (
    GroundAllocationEvent,
    GroundAllocationEventCategory,
    GroundAllocationPolicyKind,
    GroundAllocationPolicyName,
    GroundPolicyAudit,
    GroundTerminalReleaseCause,
    GroundUnscheduledReason,
    UnscheduledPair,
)

from ome.handover_policies import HandoverContext, evaluate_handover, strictly_higher
from ome.selection_policies import SelectionContext, score_candidate
from ome.types import (
    MbbTeardown,
    MbbTeardownLifecycleEvent,
    MbbTeardownState,
    ReceiveWindow,
    ReceiveWindows,
)
from ome.visibility import GroundVisibility


class UsablePair(NamedTuple):
    """One candidate pair that physics found usable at this tick, as the allocator needs it.

    The caller builds one per usable pair from OME's physics results. ``pair`` is in node-pair
    order. ``pool_a`` and ``pool_b`` name the terminal pool the pair draws on at each node.
    ``view_a`` and ``view_b`` are each node's own view of the pair, which a stated selection
    policy scores; a node that states no scheduling has no view.

    ``rule_id`` is the link rule the pair belongs to. A node's cap is counted per rule, while a
    pool's terminals are shared by every rule that draws on it. ``None`` means the caller names
    no rule for the pair; such pairs form one group at each node.
    """

    pair: tuple[str, str]
    rule_id: str | None
    pool_a: str
    pool_b: str
    range_km: float
    view_a: GroundVisibility | None
    view_b: GroundVisibility | None


@dataclass(frozen=True)
class LinkAllocationResult:
    """Result of the allocation pass for one OME tick.

    ``unscheduled_pairs`` carries one entry per usable pair that the allocator
    considered but did not schedule, with the attributed reason. Active MBB
    teardown pairs remain scheduled and are represented in ``pending_teardowns``
    rather than as unscheduled pairs.

    An association's two terminal indices are the lead node's, then the other node's
    (``_lead_and_peer``).
    """

    associations: dict[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    scheduled_pairs: frozenset[tuple[str, str]]
    unscheduled_pairs: tuple[UnscheduledPair, ...]
    policy_audit: GroundPolicyAudit
    allocation_events: tuple[GroundAllocationEvent, ...]
    lifecycle_events: tuple[MbbTeardownLifecycleEvent, ...] = ()
    # Terminals still receiving from a released peer after this tick, for the next tick.
    receive_windows: ReceiveWindows | None = None


@dataclass(frozen=True)
class _Candidate:
    pair: tuple[str, str]
    # The node whose terminal index comes first in the association: the node that chooses the
    # pair. ``peer`` is the other node.
    lead: str
    peer: str
    lead_pool: str
    peer_pool: str
    rule_id: str | None
    # The lead's view of the pair, which its policies score.
    visibility: GroundVisibility
    selection_score: float
    service_priority: int
    # The pair's rank among the lead's own candidates (ranking component ``per_gs_rank``).
    lead_rank: int
    # The size of the pool the pair draws on at the peer (ranking component
    # ``satellite_ground_terminal_capacity``).
    peer_pool_size: int
    rank_key: tuple


@dataclass(frozen=True)
class _Rejected:
    reason: GroundUnscheduledReason
    incumbent_pair: tuple[str, str] | None
    capacity_constraint: str | None


# Terminal handover failure/release reasons are final for the tick. Later
# allocator passes may reconsider capacity or hysteresis rejections after a
# same-tick release, but these state-machine outcomes must not be overwritten.
_LOCKED_REJECTION_REASONS: frozenset[GroundUnscheduledReason] = frozenset(
    {
        "replaced_by_successor",
        "successor_aborted",
        "failed_successor",
        "failed_acquire",
    }
)


def _lead_and_peer(pair: tuple[str, str], stating_nodes: AbstractSet[str]) -> tuple[str, str]:
    """Return ``(lead, peer)`` for a pair: the node that chooses it, then the other node.

    The lead is the pair's one node that states scheduling. It needs no listing of this tick's
    usable pairs, so a held pair that is no longer usable still has an orientation for its
    terminal indices and its events.
    """

    a_states = pair[0] in stating_nodes
    b_states = pair[1] in stating_nodes
    if a_states and b_states:
        raise ValueError(
            f"Pair {pair!r} has scheduling stated at both of its nodes; the allocator has no "
            "rule for that yet"
        )
    if a_states:
        return pair[0], pair[1]
    if b_states:
        return pair[1], pair[0]
    raise ValueError(
        f"Pair {pair!r} has no node that states scheduling; the allocator has no rule for that yet"
    )


def _handing_node(old_pair: tuple[str, str], successor_pair: tuple[str, str]) -> str:
    """The node that hands over: the one node an old link and its successor share."""

    shared = set(old_pair) & set(successor_pair)
    if len(shared) != 1:
        raise ValueError(
            f"Pending teardown {old_pair!r} and its successor {successor_pair!r} must share "
            "exactly one node, the node that hands over"
        )
    return next(iter(shared))


def _validate_ranking_order(
    ranking_order: Sequence[RankingComponent],
) -> tuple[RankingComponent, ...]:
    order = tuple(ranking_order)
    if not order:
        raise ValueError("link allocator ranking_order must not be empty")
    if order[-1] != "lex_pair":
        raise ValueError("link allocator ranking_order must end with 'lex_pair'")
    if len(order) == 1:
        raise ValueError("link allocator ranking_order needs a decision component before lex_pair")
    if len(set(order)) != len(order):
        raise ValueError("link allocator ranking_order must not contain duplicates")
    return order


def _rank_key(
    *,
    ranking_order: Sequence[RankingComponent],
    pair: tuple[str, str],
    selection_score: float,
    service_priority: int,
    lead_rank: int,
    peer_pool_size: int,
) -> tuple:
    values: list[object] = []
    for component in ranking_order:
        if component == "service_priority":
            values.append(service_priority)
        elif component == "selection_score":
            values.append(-selection_score)
        elif component == "per_gs_rank":
            values.append(lead_rank)
        elif component == "satellite_ground_terminal_capacity":
            values.append(peer_pool_size)
        elif component == "lex_pair":
            values.append(pair)
        else:  # pragma: no cover - Literal/model validation should prevent this.
            raise ValueError(f"Unknown ranking component {component!r}")
    return tuple(values)


def _build_candidates(
    *,
    step: int,
    usable: Sequence[UsablePair],
    stating_nodes: AbstractSet[str],
    selection_policies: Mapping[str, SelectionPolicySpec],
    service_priorities: Mapping[str, int],
    pools: Mapping[str, Mapping[str, Sequence[int]]],
    ranking_order: Sequence[RankingComponent],
) -> tuple[list[_Candidate], dict[tuple[str, str], _Candidate], set[tuple[str, str]]]:
    # One row per usable pair: (item, lead, peer, view, score). The candidate itself is built
    # once, after the lead's own ranking is known.
    by_lead: dict[str, list[tuple[UsablePair, str, GroundVisibility, float]]] = {}
    usable_set: set[tuple[str, str]] = set()

    for item in usable:
        pair = item.pair
        if pair[0] >= pair[1]:
            raise ValueError(f"Usable pair {pair!r} is not in node-pair order")
        if pair in usable_set:
            raise ValueError(f"Duplicate usable candidate for pair {pair!r}")
        usable_set.add(pair)
        lead, peer = _lead_and_peer(pair, stating_nodes)
        view = item.view_a if lead == pair[0] else item.view_b
        if view is None:
            raise ValueError(
                f"Usable pair {pair!r} carries no view for {lead}, the node that chooses it"
            )
        if not view.visible:
            raise ValueError(
                f"Usable pair {pair!r} carries a view that is not visible; visibility "
                "filtering belongs to the physics role"
            )
        if lead not in selection_policies:
            raise ValueError(f"Link allocator is missing selection policy for {lead}")
        if lead not in service_priorities:
            raise ValueError(f"Link allocator is missing service_priority for {lead}")
        score = score_candidate(
            policy=selection_policies[lead],
            visibility=view,
            context=SelectionContext(step=step, node_id=lead, peer_id=peer),
        )
        by_lead.setdefault(lead, []).append((item, peer, view, score))

    candidates: list[_Candidate] = []
    candidate_by_pair: dict[tuple[str, str], _Candidate] = {}
    for lead, rows in by_lead.items():
        rows.sort(key=lambda row: (-row[3], row[0].pair))
        for lead_rank, (item, peer, view, score) in enumerate(rows):
            lead_is_a = lead == item.pair[0]
            peer_pool = item.pool_b if lead_is_a else item.pool_a
            peer_pool_size = len(pools.get(peer, {}).get(peer_pool, ()))
            candidate = _Candidate(
                pair=item.pair,
                lead=lead,
                peer=peer,
                lead_pool=item.pool_a if lead_is_a else item.pool_b,
                peer_pool=peer_pool,
                rule_id=item.rule_id,
                visibility=view,
                selection_score=score,
                service_priority=service_priorities[lead],
                lead_rank=lead_rank,
                peer_pool_size=peer_pool_size,
                rank_key=_rank_key(
                    ranking_order=ranking_order,
                    pair=item.pair,
                    selection_score=score,
                    service_priority=service_priorities[lead],
                    lead_rank=lead_rank,
                    peer_pool_size=peer_pool_size,
                ),
            )
            candidates.append(candidate)
            candidate_by_pair[candidate.pair] = candidate

    candidates.sort(key=lambda c: c.rank_key)
    return candidates, candidate_by_pair, usable_set


def _record_rejection(
    rejected: dict[tuple[str, str], _Rejected],
    pair: tuple[str, str],
    *,
    reason: GroundUnscheduledReason,
    incumbent_pair: tuple[str, str] | None,
    capacity_constraint: str | None,
) -> None:
    rejected[pair] = _Rejected(
        reason=reason,
        incumbent_pair=incumbent_pair,
        capacity_constraint=capacity_constraint,
    )


def _make_event(
    *,
    category: GroundAllocationEventCategory,
    pair: tuple[str, str],
    lead: str,
    tenant_ids: Mapping[str, str],
    reference_bodies: Mapping[str, str],
    message: str,
    successor_pair: tuple[str, str] | None = None,
    challenger_pair: tuple[str, str] | None = None,
    policy_kind: GroundAllocationPolicyKind | None = None,
    policy_name: GroundAllocationPolicyName | None = None,
    terminal_node_id: str | None = None,
    terminal_index: int | None = None,
    remaining_arrival_ms: float | None = None,
    released_by: GroundTerminalReleaseCause | None = None,
) -> GroundAllocationEvent:
    return GroundAllocationEvent(
        category=category,
        pair=pair,
        tenant_id=tenant_ids[lead],
        reference_body=reference_bodies[lead],
        message=message,
        successor_pair=successor_pair,
        challenger_pair=challenger_pair,
        policy_kind=policy_kind,
        policy_name=policy_name,
        terminal_node_id=terminal_node_id,
        terminal_index=terminal_index,
        remaining_arrival_ms=remaining_arrival_ms,
        released_by=released_by,
    )


def _policy_audit(
    *,
    selection_policies: Mapping[str, SelectionPolicySpec],
    handover_policies: Mapping[str, HandoverPolicySpec],
    ranking_order: Sequence[RankingComponent],
    handover_modes: Mapping[str, Literal["bbm", "mbb"]],
    mbb_overlap_ticks: Mapping[str, int],
    mbb_reserve: Mapping[str, int],
    mbb_preemption: MbbPreemptionPolicy,
    successor_abort_policy: SuccessorAbortPolicy,
    cross_tenant_displacement: CrossTenantDisplacementPolicy,
    bbm_acquire_timeout_ticks: int,
    ignored_capacity_fields: Sequence[str],
) -> GroundPolicyAudit:
    handover_mode_values = set(handover_modes.values())
    return GroundPolicyAudit(
        selection_policies={k: v.name for k, v in sorted(selection_policies.items())},
        selection_policy_params={k: dict(v.params) for k, v in sorted(selection_policies.items())},
        handover_policies={k: v.name for k, v in sorted(handover_policies.items())},
        handover_policy_params={k: dict(v.params) for k, v in sorted(handover_policies.items())},
        ranking_order=tuple(ranking_order),
        handover_mode=(
            next(iter(handover_mode_values)) if len(handover_mode_values) == 1 else "mixed"
        ),
        handover_modes=dict(sorted(handover_modes.items())),
        mbb_preemption=mbb_preemption,
        successor_abort_policy=successor_abort_policy,
        cross_tenant_displacement=cross_tenant_displacement,
        mbb_overlap_ticks=max(mbb_overlap_ticks.values(), default=0),
        mbb_overlap_ticks_by_gs=dict(sorted(mbb_overlap_ticks.items())),
        mbb_reserve=max(mbb_reserve.values(), default=0),
        mbb_reserve_by_gs=dict(sorted(mbb_reserve.items())),
        bbm_acquire_timeout_ticks=bbm_acquire_timeout_ticks,
        ignored_capacity_fields=tuple(sorted(ignored_capacity_fields)),
    )


def _normalize_terminal_pools(
    *,
    terminal_pools: Mapping[str, Mapping[str, Sequence[int]]],
    stating_nodes: AbstractSet[str],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """Validate each node's named terminal pools.

    A pool of a node that states scheduling must not be empty: such a node chooses, and a
    chooser with no terminal is a configuration fault. A node that states nothing may have an
    empty pool; a pair that draws on it is refused for capacity.
    """

    normalized: dict[str, dict[str, tuple[int, ...]]] = {}
    for node_id, node_pools in sorted(terminal_pools.items()):
        normalized[node_id] = {}
        for pool_name, indices in sorted(node_pools.items()):
            pool = tuple(int(index) for index in indices)
            if not pool and node_id in stating_nodes:
                raise ValueError(f"Node {node_id} terminal pool {pool_name!r} must not be empty")
            if len(set(pool)) != len(pool):
                raise ValueError(
                    f"Node {node_id} terminal pool {pool_name!r} contains duplicate indices"
                )
            invalid = [index for index in pool if index < 0]
            if invalid:
                raise ValueError(
                    f"Node {node_id} terminal pool {pool_name!r} contains negative indices "
                    f"{invalid}"
                )
            normalized[node_id][str(pool_name)] = pool
    return normalized


def allocate_links(
    *,
    step: int,
    usable: Sequence[UsablePair],
    current_associations: Mapping[tuple[str, str], tuple[int, int]],
    pending_teardowns: MbbTeardownState,
    terminal_pools: Mapping[str, Mapping[str, Sequence[int]]],
    stating_nodes: AbstractSet[str],
    selection_policies: Mapping[str, SelectionPolicySpec],
    min_elevations: Mapping[str, float],
    handover_policies: Mapping[str, HandoverPolicySpec],
    handover_modes: Mapping[str, Literal["bbm", "mbb"]],
    mbb_overlap_ticks: Mapping[str, int],
    mbb_reserve: Mapping[str, int],
    service_priorities: Mapping[str, int],
    tenant_ids: Mapping[str, str],
    reference_bodies: Mapping[str, str],
    ranking_order: Sequence[RankingComponent],
    mbb_preemption: MbbPreemptionPolicy,
    successor_abort_policy: SuccessorAbortPolicy,
    cross_tenant_displacement: CrossTenantDisplacementPolicy,
    bbm_acquire_timeout_ticks: int,
    ignored_capacity_fields: Sequence[str],
    sim_time_unix: float,
    pair_delays_ms: Mapping[tuple[str, str], tuple[float, float]],
    receive_windows: ReceiveWindows,
) -> LinkAllocationResult:
    """Allocate links for one tick.

    This function is the state-machine mechanism. Operator policy enters only
    through named policy specs, ranking_order, and handover mode parameters.
    Scheduler state, Node Agent state, and orbital physics do not enter this
    function.

    ``stating_nodes`` names the nodes that state scheduling. The per-node policy maps hold
    their stated values; ``terminal_pools`` holds every node's named terminal pools. Within a
    node, terminal indices are one index space across its pools.

    Terminal selection: surviving links keep their indices (sticky). New
    links take the lowest free allowed index, preferring indices that
    were idle in the previously published authority state — an index vacated
    this tick may still be physically wired until the release is actuated, so
    reusing it forces the handover to break-before-make. When every free index
    was held last tick (capacity 1, or full churn), the allocator falls back
    to reuse and the Scheduler's dispatch staging owns ordering the release
    before the acquire.

    Terminal occupancy over time: a released association leaves each of its two terminals
    receiving until the signals its old peer emitted up to the release arrive (the release time
    plus that direction's light time, from ``pair_delays_ms``: each candidate pair's
    (pair[0] to pair[1], pair[1] to pair[0]) delays this tick). A terminal points at its old peer
    until then. New links prefer a terminal that is neither held nor still receiving; when the
    declared policy needs one that is still receiving, the allocator assigns it and reports a
    ``retargeted_before_last_arrival`` event with the remaining arrival time and the release
    cause. ``receive_windows`` carries the windows across ticks; the result returns the ones
    still open.
    """

    if step < 0:
        raise ValueError("Link allocator step must be non-negative")
    if mbb_preemption != "off":
        raise ValueError(
            f"mbb_preemption={mbb_preemption!r} is not implemented; "
            "the schema must reject unsupported preemption policies"
        )
    if cross_tenant_displacement != "off":
        raise ValueError(
            f"cross_tenant_displacement={cross_tenant_displacement!r} is not implemented "
            "yet; cross-tenant preemption needs an explicit priority policy"
        )
    if successor_abort_policy not in ("hard_release", "soft_retain"):
        raise ValueError(f"Unknown successor_abort_policy={successor_abort_policy!r}")
    if bbm_acquire_timeout_ticks != 1:
        raise ValueError(
            "bbm_acquire_timeout_ticks values other than 1 require the future "
            "multi-tick BBMGap wait-state algorithm"
        )
    order = _validate_ranking_order(ranking_order)
    stating = frozenset(stating_nodes)
    pools = _normalize_terminal_pools(terminal_pools=terminal_pools, stating_nodes=stating)

    for label, mapping in (
        ("handover mode", handover_modes),
        ("MBB overlap ticks", mbb_overlap_ticks),
        ("MBB reserve", mbb_reserve),
    ):
        missing = sorted(stating - set(mapping))
        extra = sorted(set(mapping) - stating)
        if missing or extra:
            detail = []
            if missing:
                detail.append("missing=" + ", ".join(missing))
            if extra:
                detail.append("extra=" + ", ".join(extra))
            raise ValueError(
                f"Link allocator {label} map does not match the nodes that state scheduling: "
                f"{'; '.join(detail)}"
            )

    for node_id in sorted(stating):
        mode = handover_modes[node_id]
        if mode not in ("bbm", "mbb"):
            raise ValueError(f"Unknown handover_mode for {node_id}: {mode!r}")
        overlap_ticks = mbb_overlap_ticks[node_id]
        reserve = mbb_reserve[node_id]
        if overlap_ticks < 0:
            raise ValueError(f"mbb_overlap_ticks for {node_id} must be >= 0")
        if reserve < 0:
            raise ValueError(f"mbb_reserve for {node_id} must be >= 0")
        # BIG HONESTY NOTE / MBB-002:
        # This allocator has a deliberate single-overlap state machine per node. When a
        # node is already in MBBOverlap, new challengers are rejected as
        # `mbb_overlap_locked`; a second reserved terminal is not consumed for a second
        # parallel overlap. Letting mbb_reserve=2+ through would silently strand
        # capacity and lie about supported gateway behavior. Remove this guard only
        # when MBB-002 adds multi-overlap pending-teardown state and proves it.
        if reserve > 1:
            raise ValueError(
                f"mbb_reserve > 1 for {node_id} requires future MBB-002 multi-overlap "
                "allocator support; current allocator supports at most one concurrent "
                "MBB overlap per node"
            )
        if mode == "mbb":
            if overlap_ticks <= 0 or reserve <= 0:
                raise ValueError(
                    f"MBB handover for {node_id} requires mbb_overlap_ticks > 0 and mbb_reserve > 0"
                )
            node_pools = pools.get(node_id)
            if not node_pools:
                raise ValueError(f"MBB handover for {node_id} requires a terminal pool")
            for pool_name, pool in node_pools.items():
                if len(pool) <= reserve:
                    raise ValueError(
                        f"MBB handover for {node_id} requires terminal capacity greater "
                        f"than mbb_reserve; pool {pool_name!r} capacity={len(pool)}, "
                        f"reserve={reserve}"
                    )
        elif reserve != 0 or overlap_ticks != 0:
            raise ValueError(
                f"BBM handover for {node_id} must carry mbb_reserve=0 and mbb_overlap_ticks=0"
            )

    leads = {_lead_and_peer(item.pair, stating)[0] for item in usable}
    missing_tenant = sorted(leads - set(tenant_ids))
    if missing_tenant:
        raise ValueError(
            "Link allocator is missing tenant_id for "
            f"{', '.join(missing_tenant)}; every unscheduled pair needs tenant scope"
        )
    missing_body = sorted(leads - set(reference_bodies))
    if missing_body:
        raise ValueError(
            "Link allocator is missing reference_body for "
            f"{', '.join(missing_body)}; every unscheduled pair needs a body anchor"
        )
    missing_handover = sorted(leads - set(handover_policies))
    if missing_handover:
        raise ValueError(
            f"Link allocator is missing handover_policy for {', '.join(missing_handover)}"
        )
    missing_min_elevation = sorted(leads - set(min_elevations))
    if missing_min_elevation:
        raise ValueError(
            f"Link allocator is missing min elevation for {', '.join(missing_min_elevation)}"
        )

    policy_audit = _policy_audit(
        selection_policies=selection_policies,
        handover_policies=handover_policies,
        ranking_order=order,
        handover_modes=handover_modes,
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
        mbb_preemption=mbb_preemption,
        successor_abort_policy=successor_abort_policy,
        cross_tenant_displacement=cross_tenant_displacement,
        bbm_acquire_timeout_ticks=bbm_acquire_timeout_ticks,
        ignored_capacity_fields=ignored_capacity_fields,
    )

    candidates, candidate_by_pair, usable_set = _build_candidates(
        step=step,
        usable=usable,
        stating_nodes=stating,
        selection_policies=selection_policies,
        service_priorities=service_priorities,
        pools=pools,
        ranking_order=order,
    )
    # A pair's place in the session's rank order. The end that does not choose compares
    # places: a lower place is a better rank.
    rank_place = {candidate.pair: place for place, candidate in enumerate(candidates)}

    occupied: dict[str, set[int]] = {}
    # Which association held each terminal index in the previously published authority
    # state. An index released this tick still receives from its old peer (and may still be
    # wired until the Scheduler actuates the release), so new links prefer indices with no
    # reception in progress (window_for below) and fall back to one only when nothing else is
    # free; the Scheduler's dispatch staging orders that release before the acquire.
    prior_holder: dict[tuple[str, int], tuple[str, str]] = {}
    for _pair, (_lead_idx, _peer_idx) in current_associations.items():
        _lead, _peer = _lead_and_peer(_pair, stating)
        prior_holder[(_lead, _lead_idx)] = _pair
        prior_holder[(_peer, _peer_idx)] = _pair
    # Why each association released this tick was released; the first cause recorded stands.
    release_cause: dict[tuple[str, str], GroundTerminalReleaseCause] = {}
    new_associations: dict[tuple[str, str], tuple[int, int]] = {}
    # Per-node association index, maintained at the three mutation points below. A
    # dict-as-ordered-set so each node's pair order matches new_associations insertion order;
    # without it every capacity check rescans every association per candidate (measured ~75%
    # of allocator runtime at scale).
    assoc_by_node: dict[str, dict[tuple[str, str], None]] = {}
    new_pending_teardowns: MbbTeardownState = {}
    rejected: dict[tuple[str, str], _Rejected] = {}
    allocation_events: list[GroundAllocationEvent] = []
    lifecycle_events: list[MbbTeardownLifecycleEvent] = []
    drop_current_pairs: set[tuple[str, str]] = set()

    # Handovers in progress, indexed by the node that hands over. `lock_by_node` holds, for
    # each such node, the first in pair order of its old link and that link's successor;
    # `protected` holds every link that is part of a handover. Both are rebuilt whenever
    # new_pending_teardowns changes, so a check costs one lookup.
    lock_by_node: dict[str, tuple[str, str]] = {}
    protected: set[tuple[str, str]] = set()
    pool_members: dict[tuple[str, str], frozenset[int]] = {}

    def rebuild_handover_index() -> None:
        lock_by_node.clear()
        protected.clear()
        for old_pair, teardown in new_pending_teardowns.items():
            node_id = _handing_node(old_pair, teardown.successor_pair)
            for handover_pair in (old_pair, teardown.successor_pair):
                protected.add(handover_pair)
                first = lock_by_node.get(node_id)
                if first is None or handover_pair < first:
                    lock_by_node[node_id] = handover_pair

    def candidate_for(pair: tuple[str, str]) -> _Candidate:
        candidate = candidate_by_pair.get(pair)
        if candidate is None:
            raise ValueError(
                f"Internal allocator invariant violated: association {pair!r} is held "
                "but has no usable candidate."
            )
        return candidate

    def pool_of(node_id: str, pool_name: str) -> tuple[int, ...]:
        return pools.get(node_id, {}).get(pool_name, ())

    def members_of(node_id: str, pool_name: str) -> frozenset[int]:
        key = (node_id, pool_name)
        members = pool_members.get(key)
        if members is None:
            members = frozenset(pool_of(node_id, pool_name))
            pool_members[key] = members
        return members

    def index_at(pair: tuple[str, str], node_id: str) -> int:
        lead_idx, peer_idx = new_associations[pair]
        return lead_idx if node_id == candidate_for(pair).lead else peer_idx

    def add_existing(pair: tuple[str, str], indices: tuple[int, int]) -> None:
        candidate = candidate_for(pair)
        ends = (
            (candidate.lead, candidate.lead_pool, indices[0]),
            (candidate.peer, candidate.peer_pool, indices[1]),
        )
        for node_id, pool_name, index in ends:
            if node_id not in pools:
                raise ValueError(f"Missing terminal pools for {node_id}")
            if index not in members_of(node_id, pool_name):
                raise ValueError(
                    f"Association {pair!r} uses terminal index {index} at {node_id}, but "
                    f"{node_id}.{pool_name} holds {pool_of(node_id, pool_name)}"
                )
            if index in occupied.setdefault(node_id, set()):
                raise ValueError(f"Duplicate terminal occupancy {node_id}[{index}]")
        for node_id, _pool_name, index in ends:
            occupied[node_id].add(index)
            assoc_by_node.setdefault(node_id, {})[pair] = None
        new_associations[pair] = indices

    def remove_association(pair: tuple[str, str]) -> tuple[int, int]:
        candidate = candidate_for(pair)
        indices = new_associations.pop(pair)
        for node_id, index in ((candidate.lead, indices[0]), (candidate.peer, indices[1])):
            occupied.setdefault(node_id, set()).discard(index)
            assoc_by_node.get(node_id, {}).pop(pair, None)
        if new_pending_teardowns.pop(pair, None) is not None:
            rebuild_handover_index()
        return indices

    def released(pair: tuple[str, str], cause: GroundTerminalReleaseCause) -> None:
        release_cause.setdefault(pair, cause)

    def light_time_into_ms(pair: tuple[str, str], node_id: str) -> float:
        try:
            a_to_b, b_to_a = pair_delays_ms[pair]
        except KeyError as exc:
            raise ValueError(
                f"released pair {pair!r} has no delays this tick; its terminals' "
                "reception windows cannot be bounded"
            ) from exc
        return a_to_b if node_id == pair[1] else b_to_a

    def window_for(node_id: str, index: int) -> ReceiveWindow | None:
        """The reception still arriving on a terminal: from a release earlier this tick, or a
        window carried from an earlier tick that has not ended."""
        holder = prior_holder.get((node_id, index))
        if holder is not None and holder not in new_associations:
            cause = release_cause.get(holder)
            if cause is None:
                raise ValueError(
                    f"terminal {node_id}[{index}] was released from {holder!r} with no recorded "
                    "cause; the allocator must record every release"
                )
            return ReceiveWindow(
                until_unix=sim_time_unix + light_time_into_ms(holder, node_id) / 1000.0,
                old_pair=holder,
                released_by=cause,
            )
        window = receive_windows.get((node_id, index))
        if window is not None and window.until_unix > sim_time_unix:
            return window
        return None

    def retargeted(node_id: str, index: int, candidate: _Candidate) -> None:
        window = window_for(node_id, index)
        if window is None or window.old_pair == candidate.pair:
            # Free, or the same peer taking its terminal back: the pointing does not change.
            return
        remaining_ms = (window.until_unix - sim_time_unix) * 1000.0
        policy = {
            "bbm_gap": ("handover_mode", "bbm"),
            "teardown_completed": ("handover_mode", "mbb"),
        }.get(window.released_by)
        allocation_events.append(
            _make_event(
                category="retargeted_before_last_arrival",
                pair=window.old_pair,
                lead=candidate.lead,
                tenant_ids=tenant_ids,
                reference_bodies=reference_bodies,
                message=(
                    f"terminal {node_id}[{index}] turned from {window.old_pair!r} to "
                    f"{candidate.pair!r} while the old peer's signals were still arriving for "
                    f"{remaining_ms:.3f} ms; released by {window.released_by}"
                ),
                successor_pair=candidate.pair,
                policy_kind=policy[0] if policy else None,
                policy_name=policy[1] if policy else None,
                terminal_node_id=node_id,
                terminal_index=index,
                remaining_arrival_ms=remaining_ms,
                released_by=window.released_by,
            )
        )

    def has_free_terminal(node_id: str, pool_name: str) -> bool:
        held = occupied.get(node_id, ())
        return any(index not in held for index in pool_of(node_id, pool_name))

    def free_terminal(node_id: str, pool_name: str) -> int | None:
        """A free index of the pool, preferring one that is not still receiving from a
        released peer."""
        held = occupied.setdefault(node_id, set())
        pool = pool_of(node_id, pool_name)
        quiet = next(
            (index for index in pool if index not in held and window_for(node_id, index) is None),
            None,
        )
        if quiet is not None:
            return quiet
        return next((index for index in pool if index not in held), None)

    def allocate_new(candidate: _Candidate) -> bool:
        lead_idx = free_terminal(candidate.lead, candidate.lead_pool)
        peer_idx = free_terminal(candidate.peer, candidate.peer_pool)
        if lead_idx is None or peer_idx is None:
            return False
        retargeted(candidate.lead, lead_idx, candidate)
        retargeted(candidate.peer, peer_idx, candidate)
        occupied[candidate.lead].add(lead_idx)
        occupied[candidate.peer].add(peer_idx)
        new_associations[candidate.pair] = (lead_idx, peer_idx)
        assoc_by_node.setdefault(candidate.lead, {})[candidate.pair] = None
        assoc_by_node.setdefault(candidate.peer, {})[candidate.pair] = None
        rejected.pop(candidate.pair, None)
        return True

    def handover_mode_for(node_id: str) -> Literal["bbm", "mbb"]:
        try:
            return handover_modes[node_id]
        except KeyError:
            raise ValueError(f"Node {node_id} states no handover mode") from None

    def mbb_overlap_ticks_for(node_id: str) -> int:
        return mbb_overlap_ticks[node_id] if handover_mode_for(node_id) == "mbb" else 0

    def cap_of(node_id: str, pool_name: str) -> int:
        """A node's cap on a rule: the pool's terminals, less the make-before-break reserve
        the node states."""
        size = len(pool_of(node_id, pool_name))
        if node_id in stating and handover_modes[node_id] == "mbb":
            return size - mbb_reserve[node_id]
        return size

    def rejection_locked(pair: tuple[str, str]) -> bool:
        rejection = rejected.get(pair)
        return rejection is not None and rejection.reason in _LOCKED_REJECTION_REASONS

    def end_load(
        candidate: _Candidate, node_id: str, pool_name: str
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], bool, bool]:
        """What one node already holds against a candidate: the pool's occupants, the node's
        steady links of the candidate's rule, whether those reach its cap, and whether the
        pool has a free terminal.

        A cap is counted per rule and a pool is shared by rules, so both tests are needed. A
        link the node itself is handing over does not count against its cap.
        """
        members = members_of(node_id, pool_name)
        occupants = [
            pair for pair in assoc_by_node.get(node_id, ()) if index_at(pair, node_id) in members
        ]
        steady = [
            pair
            for pair in occupants
            if candidate_for(pair).rule_id == candidate.rule_id
            and not (
                pair in new_pending_teardowns
                and _handing_node(pair, new_pending_teardowns[pair].successor_pair) == node_id
            )
        ]
        cap_reached = len(steady) >= cap_of(node_id, pool_name)
        return occupants, steady, cap_reached, has_free_terminal(node_id, pool_name)

    def worst_of(pairs: Sequence[tuple[str, str]]) -> _Candidate | None:
        if not pairs:
            return None
        return max((candidate_for(pair) for pair in pairs), key=lambda c: c.rank_key)

    def check_end(
        candidate: _Candidate, node_id: str, pool_name: str, *, chooses: bool
    ) -> tuple[_Candidate | None, _Rejected | None]:
        """Decide one node's end of a candidate pair.

        Returns the held link the candidate takes the place of (``None`` when the node has
        room) and a rejection (``None`` when the candidate may link at this end).

        Every case decides with the one base rule, a strictly higher score. A node that
        chooses the pair applies its handover policy to its own scores. A node that does not
        choose compares places in the session's rank order.

        A capacity refusal is published as ``gs_capacity`` at a node that chooses and as
        ``sat_capacity`` at a node that does not. Both names are part of the published reason
        vocabulary, so they stay until that vocabulary loses its ground and satellite roles.
        """
        occupants, steady, cap_reached, terminal_free = end_load(candidate, node_id, pool_name)
        if not cap_reached and terminal_free:
            return None, None

        capacity_text = f"{node_id}.{pool_name}"
        # The links whose release would make room: the node's links of the same rule when its
        # cap is reached, and any occupant of the pool when only the terminals are used up.
        # A link in a handover cannot be taken, and no link of another tenant.
        takeable = [
            pair
            for pair in (steady if cap_reached else occupants)
            if pair not in protected
            and tenant_ids[candidate_for(pair).lead] == tenant_ids[candidate.lead]
        ]
        incumbent = worst_of(takeable)

        if chooses:
            if incumbent is None:
                return None, _Rejected(
                    reason="gs_capacity", incumbent_pair=None, capacity_constraint=capacity_text
                )
            handover_policy = handover_policies[node_id]
            decision = evaluate_handover(
                policy=handover_policy,
                incumbent_score=incumbent.selection_score,
                challenger_score=candidate.selection_score,
                context=HandoverContext(
                    step=step,
                    node_id=node_id,
                    incumbent_pair=incumbent.pair,
                    challenger_pair=candidate.pair,
                    incumbent_visibility=incumbent.visibility,
                    challenger_visibility=candidate.visibility,
                    min_elevation_deg=min_elevations[node_id],
                ),
            )
            if decision.action == "hold":
                if decision.unscheduled_reason is None:
                    raise ValueError(
                        f"Handover policy {handover_policy.name!r} held {candidate.pair!r} "
                        "but did not provide an unscheduled reason"
                    )
                return None, _Rejected(
                    reason=decision.unscheduled_reason,
                    incumbent_pair=incumbent.pair,
                    capacity_constraint=None,
                )
            if decision.action != "displace":
                raise ValueError(
                    f"Handover policy {handover_policy.name!r} returned unknown action "
                    f"{decision.action!r}"
                )
            if handover_mode_for(node_id) == "mbb" and not terminal_free:
                return None, _Rejected(
                    reason="bbm_no_spare",
                    incumbent_pair=incumbent.pair,
                    capacity_constraint=capacity_text,
                )
            return incumbent, None

        # The node does not choose the pair. A refusal names the pool's worst-ranked occupant,
        # whether or not that link could be taken.
        if incumbent is None or not strictly_higher(
            -rank_place[candidate.pair], -rank_place[incumbent.pair]
        ):
            blocker = worst_of(occupants)
            return None, _Rejected(
                reason="sat_capacity",
                incumbent_pair=blocker.pair if blocker is not None else None,
                capacity_constraint=capacity_text,
            )
        return incumbent, None

    def emit_bbm_gap(incumbent: _Candidate, challenger: _Candidate) -> None:
        allocation_events.append(
            _make_event(
                category="bbm_gap",
                pair=incumbent.pair,
                lead=challenger.lead,
                tenant_ids=tenant_ids,
                reference_bodies=reference_bodies,
                message=(
                    f"BBM released incumbent {incumbent.pair!r} before acquiring "
                    f"challenger {challenger.pair!r}; timeout_ticks={bbm_acquire_timeout_ticks}. "
                    "BBMGap is an immediate one-tick release/acquire transition."
                ),
                successor_pair=challenger.pair,
                challenger_pair=challenger.pair,
                policy_kind="handover_mode",
                policy_name="bbm",
            )
        )

    def emit_incumbent_lost(pair: tuple[str, str], message: str) -> None:
        released(pair, "incumbent_lost")
        lead, _peer = _lead_and_peer(pair, stating)
        allocation_events.append(
            _make_event(
                category="incumbent_lost",
                pair=pair,
                lead=lead,
                tenant_ids=tenant_ids,
                reference_bodies=reference_bodies,
                message=message,
            )
        )

    def authority_before_for_pairs(
        old_pair: tuple[str, str], successor_pair: tuple[str, str]
    ) -> dict[str, dict[str, object]]:
        def one(pair: tuple[str, str]) -> dict[str, object]:
            indices = current_associations.get(pair)
            return {
                "pair": list(pair),
                "scheduled": pair in current_associations,
                "pending_teardown": pair in pending_teardowns,
                "visible": pair in usable_set,
                "terminal_indices": list(indices) if indices is not None else None,
            }

        return {
            "old_pair": one(old_pair),
            "successor_pair": one(successor_pair),
        }

    def emit_mbb_lifecycle_terminal(
        *,
        category: GroundAllocationEventCategory,
        old_pair: tuple[str, str],
        successor_pair: tuple[str, str],
        handing_node: str,
        message: str,
        source_allocation_event_category: GroundAllocationEventCategory | None = None,
    ) -> None:
        lifecycle_events.append(
            MbbTeardownLifecycleEvent(
                category=category,
                old_pair=old_pair,
                successor_pair=successor_pair,
                gs_id=handing_node,
                message=message,
                source_allocation_event_category=source_allocation_event_category or category,
                authority_before=authority_before_for_pairs(old_pair, successor_pair),
                terminal_indices={
                    key: tuple(value)
                    for key, value in (
                        ("old_pair", current_associations.get(old_pair)),
                        ("successor_pair", current_associations.get(successor_pair)),
                    )
                    if value is not None
                },
            )
        )

    # Prior-state recovery: resolve in-flight MBB teardown state before new
    # candidates compete. This is a state-machine transition, not selection.
    for pair in sorted(pending_teardowns):
        if pair not in current_associations:
            raise ValueError(f"Pending teardown {pair!r} is missing from current associations")
        teardown = pending_teardowns[pair]
        if teardown.start_step > step:
            raise ValueError(f"Pending teardown {pair!r} starts in the future")
        successor = teardown.successor_pair
        if successor == pair:
            raise ValueError(f"Pending teardown {pair!r} names itself as successor")
        handing_node = _handing_node(pair, successor)
        pair_lead, _pair_peer = _lead_and_peer(pair, stating)
        pair_visible = pair in usable_set
        successor_visible = successor in usable_set
        successor_current = successor in current_associations and successor not in pending_teardowns
        elapsed = step - teardown.start_step
        if handover_mode_for(handing_node) != "mbb":
            raise ValueError(
                f"Pending MBB teardown {pair!r} exists for {handing_node}, which hands over "
                "break before make; runtime handover policy and allocator state are "
                "inconsistent"
            )

        if not successor_visible or not successor_current:
            category = "failed_successor" if not successor_current else "successor_aborted"
            message = (
                f"MBB successor {successor!r} did not survive prior-state recovery; "
                f"successor_abort_policy={successor_abort_policy}"
            )
            allocation_events.append(
                _make_event(
                    category=category,
                    pair=pair,
                    lead=pair_lead,
                    tenant_ids=tenant_ids,
                    reference_bodies=reference_bodies,
                    message=message,
                    successor_pair=successor,
                    policy_kind="successor_abort_policy",
                    policy_name=successor_abort_policy,
                )
            )
            emit_mbb_lifecycle_terminal(
                category=category,
                old_pair=pair,
                successor_pair=successor,
                handing_node=handing_node,
                message=message,
            )
            if not pair_visible:
                emit_incumbent_lost(
                    pair,
                    f"Scheduled MBB incumbent {pair!r} lost physical visibility during "
                    "prior-state recovery",
                )
            if successor_current and not successor_visible:
                emit_incumbent_lost(
                    successor,
                    f"Scheduled MBB successor {successor!r} lost physical visibility "
                    "during prior-state recovery",
                )
            if successor_visible and not successor_current:
                successor_lead, _successor_peer = _lead_and_peer(successor, stating)
                allocation_events.append(
                    _make_event(
                        category="failed_acquire",
                        pair=successor,
                        lead=successor_lead,
                        tenant_ids=tenant_ids,
                        reference_bodies=reference_bodies,
                        message=(
                            f"MBB successor {successor!r} was visible but was not present "
                            "in current associations during prior-state recovery"
                        ),
                        successor_pair=successor,
                        policy_kind="successor_abort_policy",
                        policy_name=successor_abort_policy,
                    )
                )
            drop_current_pairs.add(pair)
            drop_current_pairs.add(successor)
            released(pair, category)
            released(successor, category)
            if successor_visible:
                _record_rejection(
                    rejected,
                    successor,
                    reason="failed_acquire",
                    incumbent_pair=pair,
                    capacity_constraint=None,
                )
            if pair_visible and successor_abort_policy == "soft_retain":
                add_existing(pair, current_associations[pair])
            elif pair_visible:
                _record_rejection(
                    rejected,
                    pair,
                    reason=category,
                    incumbent_pair=successor,
                    capacity_constraint=None,
                )
            continue

        if elapsed >= mbb_overlap_ticks_for(handing_node) or not pair_visible:
            drop_current_pairs.add(pair)
            released(pair, "teardown_completed")
            completion_message = (
                f"MBB teardown completed for old pair {pair!r}; successor {successor!r} "
                f"remains scheduled; elapsed_ticks={elapsed}; old_pair_visible={pair_visible}"
            )
            allocation_events.append(
                _make_event(
                    category="teardown_completed",
                    pair=pair,
                    lead=pair_lead,
                    tenant_ids=tenant_ids,
                    reference_bodies=reference_bodies,
                    message=completion_message,
                    successor_pair=successor,
                    policy_kind="handover_mode",
                    policy_name="mbb",
                )
            )
            emit_mbb_lifecycle_terminal(
                category="teardown_completed",
                old_pair=pair,
                successor_pair=successor,
                handing_node=handing_node,
                message=completion_message,
            )
            if not pair_visible:
                emit_incumbent_lost(
                    pair,
                    f"Scheduled MBB teardown pair {pair!r} lost physical visibility",
                )
            if pair_visible:
                _record_rejection(
                    rejected,
                    pair,
                    reason="replaced_by_successor",
                    incumbent_pair=successor,
                    capacity_constraint=None,
                )
            continue

        add_existing(pair, current_associations[pair])
        new_pending_teardowns[pair] = teardown
    rebuild_handover_index()

    # Steady-state continuity: usable current links not in teardown survive as
    # incumbents. They may still be displaced later by handover policy.
    for pair, indices in sorted(current_associations.items()):
        if pair in pending_teardowns or pair in drop_current_pairs:
            continue
        if pair not in usable_set:
            emit_incumbent_lost(
                pair,
                f"Scheduled incumbent {pair!r} lost physical visibility",
            )
            continue
        add_existing(pair, indices)

    # Fail-safe convergence cap. Typical ticks complete in one or two passes;
    # the cap prevents a logic bug from spinning forever.
    max_candidate_passes = max(
        1,
        len(candidates) + len(current_associations) + len(pending_teardowns) + 1,
    )
    for _pass_idx in range(max_candidate_passes):
        progress = False
        for candidate in candidates:
            if candidate.pair in new_associations or rejection_locked(candidate.pair):
                continue

            # The node that does not choose is checked first, then the node that chooses, so
            # a pair refused at both ends carries the first end's reason.
            ends = (
                (candidate.peer, candidate.peer_pool, False),
                (candidate.lead, candidate.lead_pool, True),
            )

            # A node in a handover of its own takes no other link until the handover ends,
            # whether or not it has room.
            locked = False
            for node_id, pool_name in (
                (candidate.lead, candidate.lead_pool),
                (candidate.peer, candidate.peer_pool),
            ):
                handover_pair = lock_by_node.get(node_id)
                if handover_pair is not None:
                    _record_rejection(
                        rejected,
                        candidate.pair,
                        reason="mbb_overlap_locked",
                        incumbent_pair=handover_pair,
                        capacity_constraint=f"{node_id}.{pool_name}",
                    )
                    locked = True
                    break
            if locked:
                continue

            # Both ends decide before any link is released.
            displaced: list[tuple[str, str, bool, _Candidate]] = []
            rejection: _Rejected | None = None
            for node_id, pool_name, chooses in ends:
                incumbent, rejection = check_end(candidate, node_id, pool_name, chooses=chooses)
                if rejection is not None:
                    break
                if incumbent is not None:
                    displaced.append((node_id, pool_name, chooses, incumbent))
            if rejection is not None:
                _record_rejection(
                    rejected,
                    candidate.pair,
                    reason=rejection.reason,
                    incumbent_pair=rejection.incumbent_pair,
                    capacity_constraint=rejection.capacity_constraint,
                )
                continue

            # A choosing node that breaks before it makes releases its held link now. That
            # link is final for the tick.
            for node_id, _pool_name, chooses, incumbent in displaced:
                if chooses and handover_mode_for(node_id) == "bbm":
                    released(incumbent.pair, "bbm_gap")
                    remove_association(incumbent.pair)
                    if incumbent.pair in usable_set:
                        _record_rejection(
                            rejected,
                            incumbent.pair,
                            reason="replaced_by_successor",
                            incumbent_pair=candidate.pair,
                            capacity_constraint=None,
                        )
                    emit_bbm_gap(incumbent, candidate)
                    progress = True

            # A node that does not choose releases its held link at once. That link's pair
            # may be considered again in this tick.
            for node_id, pool_name, chooses, incumbent in displaced:
                if not chooses:
                    released(incumbent.pair, "sat_capacity")
                    remove_association(incumbent.pair)
                    _record_rejection(
                        rejected,
                        incumbent.pair,
                        reason="sat_capacity",
                        incumbent_pair=candidate.pair,
                        capacity_constraint=f"{node_id}.{pool_name}",
                    )
                    progress = True

            if not allocate_new(candidate):
                raise ValueError(
                    f"Allocator selected {candidate.pair!r} after capacity arbitration "
                    "but could not allocate endpoint terminal indices"
                )
            progress = True

            # A choosing node that makes before it breaks keeps its held link for the overlap.
            for node_id, _pool_name, chooses, incumbent in displaced:
                if chooses and handover_mode_for(node_id) == "mbb":
                    new_pending_teardowns[incumbent.pair] = MbbTeardown(
                        start_step=step,
                        successor_pair=candidate.pair,
                    )
                    rebuild_handover_index()
                    allocation_events.append(
                        _make_event(
                            category="mbb_overlap_started",
                            pair=incumbent.pair,
                            lead=candidate.lead,
                            tenant_ids=tenant_ids,
                            reference_bodies=reference_bodies,
                            message=(
                                f"MBB overlap started for incumbent {incumbent.pair!r} "
                                f"with successor {candidate.pair!r}; "
                                f"overlap_ticks={mbb_overlap_ticks_for(node_id)}"
                            ),
                            successor_pair=candidate.pair,
                            challenger_pair=candidate.pair,
                            policy_kind="handover_mode",
                            policy_name="mbb",
                        )
                    )

        if not progress:
            break
    else:
        raise ValueError(
            "Link allocator did not converge after deterministic capacity arbitration passes"
        )

    unscheduled: list[UnscheduledPair] = []
    for pair in sorted(usable_set - set(new_associations)):
        candidate = candidate_by_pair.get(pair)
        if candidate is None:
            raise ValueError(
                f"Usable unscheduled pair {pair!r} has no allocation candidate; "
                "visibility and allocation inputs are inconsistent"
            )
        rejection = rejected.get(pair)
        if rejection is None:
            peer_occupants, _steady, _cap_reached, peer_terminal_free = end_load(
                candidate, candidate.peer, candidate.peer_pool
            )
            _occupants, lead_steady, lead_cap_reached, lead_terminal_free = end_load(
                candidate, candidate.lead, candidate.lead_pool
            )
            if not peer_terminal_free:
                blocker = worst_of(peer_occupants)
                rejection = _Rejected(
                    reason="sat_capacity",
                    incumbent_pair=blocker.pair if blocker is not None else None,
                    capacity_constraint=f"{candidate.peer}.{candidate.peer_pool}",
                )
            elif lead_cap_reached or not lead_terminal_free:
                incumbent = worst_of([p for p in lead_steady if p not in new_pending_teardowns])
                rejection = _Rejected(
                    reason="gs_capacity",
                    incumbent_pair=incumbent.pair if incumbent else None,
                    capacity_constraint=f"{candidate.lead}.{candidate.lead_pool}",
                )
            else:
                raise ValueError(
                    f"Allocator could not attribute rejection reason for {pair!r}. "
                    "This is an allocator logic bug; fix attribution rather than "
                    "emitting a generic reason."
                )
        unscheduled.append(
            UnscheduledPair(
                pair=pair,
                tenant_id=tenant_ids[candidate.lead],
                reference_body=reference_bodies[candidate.lead],
                unscheduled_reason=rejection.reason,
                incumbent_pair=rejection.incumbent_pair,
                capacity_constraint=rejection.capacity_constraint,
            )
        )

    # Windows still open for the next tick: those carried from earlier ticks that have not
    # ended, and those this tick's releases opened, on terminals no new link took.
    held = set()
    for pair, (lead_idx, peer_idx) in new_associations.items():
        candidate = candidate_for(pair)
        held.add((candidate.lead, lead_idx))
        held.add((candidate.peer, peer_idx))
    open_windows: ReceiveWindows = {
        key: window
        for key, window in receive_windows.items()
        if window.until_unix > sim_time_unix and key not in held
    }
    for key, holder in prior_holder.items():
        if holder in new_associations or key in held:
            continue
        window = window_for(*key)
        if window is not None:
            open_windows[key] = window

    return LinkAllocationResult(
        associations=new_associations,
        pending_teardowns=new_pending_teardowns,
        scheduled_pairs=frozenset(new_associations),
        unscheduled_pairs=tuple(unscheduled),
        policy_audit=policy_audit,
        allocation_events=tuple(allocation_events),
        lifecycle_events=tuple(lifecycle_events),
        receive_windows=open_windows,
    )
