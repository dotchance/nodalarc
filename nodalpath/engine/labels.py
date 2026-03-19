"""SR-MPLS label allocation and forwarding table generation (RFC 8402).

NodalPath implements centralized SR-TE: the ground segment (PCE) computes
full explicit paths and builds label stacks at the ingress LER. This module
handles label allocation and label stack assembly.

Label Types (RFC 8402)
======================

**Node SIDs** — globally unique, one per node (satellite or ground station).
Identify the node itself. Used as the bottom-of-stack label for egress
delivery: when the egress node pops its node SID, the inner IP packet is
delivered locally.

**Adjacency SIDs** — per-interface, locally significant. Each ISL/ground
interface on a node gets a unique adjacency SID that encodes "exit this
node via this specific interface." Used for all transit hops in the label
stack.

Label Stack Assembly
====================

For a path: ingress → hop_B (via isl0) → hop_C (via isl2) → egress_D

The ingress pushes:
  [adj_SID_B_isl0, adj_SID_C_isl2, node_SID_D]

- Transit hops pop their adjacency SID and forward out the encoded interface
- The egress pops its node SID and delivers the IP packet locally
- No SWAP entries at transit nodes — only POP
- No per-path state at transit nodes — adjacency SIDs are fixed per interface

This eliminates label conflicts: each node's adjacency SID appears at most
once in any stack, and the FRR rule is always POP → forward out one specific
interface. Multiple LSPs through the same node use different adjacency SIDs
if they exit via different interfaces.

FRR Configuration (per node)
=============================

Each node gets:
- One ``mpls lsp <adj_SID> <nexthop> implicit-null`` per ISL interface
  (POP adjacency SID → forward out that interface)
- One ``mpls lsp <node_SID> <own_loopback> implicit-null``
  (POP node SID → deliver locally)
- LER ingress rules via ``ip route <prefix> <nexthop> label <stack>``
  (push full label stack at ingress)

Total FRR rules per node: ~4 (3 ISL adj-SIDs + 1 node SID) regardless
of how many LSPs traverse the node.
"""

from __future__ import annotations

import logging

from nodalpath.engine.graph import TopologyGraph
from nodalpath.models.almanac import IngressRule, LabelBinding
from nodalpath.models.path import ComputedPath

log = logging.getLogger(__name__)


# --- SID range accessors ---


def _srgb_base() -> int:
    from nodalpath.platform import get_nodalpath_config

    return get_nodalpath_config().satellite_sid_range_start


def _gs_sid_base() -> int:
    from nodalpath.platform import get_nodalpath_config

    return get_nodalpath_config().ground_station_sid_range_start


def _adj_sid_base() -> int:
    from nodalpath.platform import get_nodalpath_config

    return get_nodalpath_config().adjacency_sid_range_start


# --- Node SID computation (unchanged from original) ---


def compute_sid(
    node_id: str,
    node_type: str,
    plane: int | None = None,
    slot: int | None = None,
    gs_index: int | None = None,
    sats_per_plane: int = 11,
) -> int:
    """Compute the SR node SID for a given node.

    Satellite SID = SRGB_BASE + (plane * sats_per_plane + slot) + 1
    Ground station SID = GS_SID_BASE + gs_index
    """
    if node_type == "satellite":
        if plane is None or slot is None:
            raise ValueError(f"Satellite {node_id} requires plane and slot")
        return _srgb_base() + (plane * sats_per_plane + slot) + 1
    elif node_type == "ground_station":
        if gs_index is None:
            raise ValueError(f"Ground station {node_id} requires gs_index")
        return _gs_sid_base() + gs_index
    else:
        raise ValueError(f"Unknown node_type: {node_type}")


# --- Adjacency SID computation ---

# Maximum interfaces per node (isl0, isl1, isl2, gnd0, terr0, ...)
MAX_INTERFACES_PER_NODE = 8


def compute_adjacency_sid(node_index: int, iface_index: int) -> int:
    """Compute the adjacency SID for a specific interface on a specific node.

    ADJ_SID = ADJ_SID_BASE + (node_index * MAX_INTERFACES_PER_NODE + iface_index)
    """
    return _adj_sid_base() + (node_index * MAX_INTERFACES_PER_NODE + iface_index)


# Well-known interface name → index mapping
_IFACE_INDEX = {"isl0": 0, "isl1": 1, "isl2": 2, "isl3": 3, "gnd0": 4, "terr0": 5, "terr1": 6}


def build_adjacency_sid_map(
    node_registry: dict,
    interface_map: dict[tuple[str, str], tuple[str, str]],
) -> dict[tuple[str, str], int]:
    """Build a map of (node_id, interface_name) → adjacency SID.

    Scans all interfaces referenced in the interface_map to assign
    deterministic adjacency SIDs.
    """
    # Assign a stable node index to each node (sorted for determinism)
    node_ids = sorted(node_registry.keys())
    node_index_map = {nid: idx for idx, nid in enumerate(node_ids)}

    adj_map: dict[tuple[str, str], int] = {}
    for (node_a, node_b), (iface_a, iface_b) in interface_map.items():
        for node_id, iface_name in [(node_a, iface_a), (node_b, iface_b)]:
            key = (node_id, iface_name)
            if key in adj_map:
                continue
            node_idx = node_index_map.get(node_id)
            if node_idx is None:
                continue
            iface_idx = _IFACE_INDEX.get(iface_name)
            if iface_idx is None:
                log.warning(
                    "Unknown interface name %s on %s, skipping adj-SID", iface_name, node_id
                )
                continue
            adj_map[key] = compute_adjacency_sid(node_idx, iface_idx)

    return adj_map


# --- Label stack assembly ---


def path_to_label_stack(
    path: ComputedPath,
    adj_sid_map: dict[tuple[str, str], int],
) -> list[int]:
    """Build the SR-TE label stack for a computed path.

    Returns [adj_SID_hop1, adj_SID_hop2, ..., node_SID_egress].

    Transit hops use adjacency SIDs (encoding the exit interface).
    The egress (last hop) uses its node SID (for local delivery).
    The ingress (first hop) is NOT in the stack — it pushes the stack.
    """
    if len(path.hops) < 2:
        return []

    stack: list[int] = []
    for i, hop in enumerate(path.hops[1:], start=1):
        is_egress = i == len(path.hops) - 1
        if is_egress:
            # Bottom of stack: egress node SID (deliver locally)
            stack.append(hop.sid)
        else:
            # Transit: adjacency SID for this hop's out_interface
            adj_key = (hop.node_id, hop.out_interface)
            adj_sid = adj_sid_map.get(adj_key)
            if adj_sid is None:
                log.warning(
                    "No adjacency SID for %s:%s in path %s, falling back to node SID",
                    hop.node_id,
                    hop.out_interface,
                    path.path_id,
                )
                stack.append(hop.sid)
            else:
                stack.append(adj_sid)

    return stack


# --- LSR binding generation ---


def build_lsr_bindings(
    node_id: str,
    paths: list[ComputedPath],
    graph: TopologyGraph,
    adj_sid_map: dict[tuple[str, str], int] | None = None,
) -> list[LabelBinding]:
    """Build MPLS POP bindings for a node's adjacency SIDs and node SID.

    In the SR-TE label stack model, transit nodes have NO per-path state.
    Each node needs:
    - One POP per adjacency SID (one per interface): pop and forward out
      the encoded interface
    - One POP for its node SID: pop and deliver locally (egress)

    These are FIXED regardless of how many LSPs traverse the node.
    """
    bindings: list[LabelBinding] = []
    node_sid = graph.node_sids.get(node_id)

    # Node SID POP (egress: deliver locally via loopback)
    if node_sid is not None:
        bindings.append(
            LabelBinding(
                in_label=node_sid,
                action="pop",
                out_label=None,
                out_interface="lo",
            )
        )

    # Adjacency SID POPs (transit: forward out the specific interface)
    if adj_sid_map is not None:
        seen_adj: set[int] = set()
        for (nid, iface_name), adj_sid in sorted(adj_sid_map.items()):
            if nid != node_id:
                continue
            if adj_sid in seen_adj:
                continue
            seen_adj.add(adj_sid)
            bindings.append(
                LabelBinding(
                    in_label=adj_sid,
                    action="pop",
                    out_label=None,
                    out_interface=iface_name,
                )
            )

    return bindings


def build_ler_ingress_rules(
    node_id: str,
    paths: list[ComputedPath],
    graph: TopologyGraph,
    prefix_map: dict[str, list[str]],
    adj_sid_map: dict[tuple[str, str], int] | None = None,
) -> list[IngressRule]:
    """Build LER ingress rules for any node that is a path source.

    For each reachable prefix, create an IngressRule using the best
    (lowest-latency) path to an advertising node.

    Multi-prefix: a destination node may advertise multiple prefixes —
    each gets its own IngressRule via the same path.

    Shared prefix: if multiple nodes advertise the same prefix (e.g.
    0.0.0.0/0), the nearest reachable advertiser is chosen.
    """
    # Index paths from this node by destination
    paths_from_me: dict[str, ComputedPath] = {}
    for path in paths:
        if path.src_node_id != node_id:
            continue
        if not path.label_stack:
            continue
        # Keep best (lowest latency) path per destination
        existing = paths_from_me.get(path.dst_node_id)
        if existing is None or path.total_latency_ms < existing.total_latency_ms:
            paths_from_me[path.dst_node_id] = path

    # Build reverse map: prefix → [(advertising_node_id, path)]
    prefix_candidates: dict[str, list[tuple[str, ComputedPath]]] = {}
    for adv_node_id, prefixes in prefix_map.items():
        if adv_node_id == node_id:
            continue  # Skip self-advertising
        path = paths_from_me.get(adv_node_id)
        if path is None:
            continue  # Unreachable advertiser
        for pfx in prefixes:
            prefix_candidates.setdefault(pfx, []).append((adv_node_id, path))

    # For each prefix, pick the best (lowest-latency) advertiser
    rules: list[IngressRule] = []
    for pfx, candidates in prefix_candidates.items():
        best_node, best_path = min(candidates, key=lambda c: c[1].total_latency_ms)
        out_interface = best_path.hops[0].out_interface or ""

        # Build SR-TE label stack using adjacency SIDs if available
        if adj_sid_map is not None:
            sr_stack = path_to_label_stack(best_path, adj_sid_map)
        else:
            sr_stack = list(best_path.label_stack)

        push_label = sr_stack[0] if sr_stack else 0
        rules.append(
            IngressRule(
                dst_prefix=pfx,
                push_label=push_label,
                out_interface=out_interface,
                label_stack=sr_stack,
            )
        )

    return rules
