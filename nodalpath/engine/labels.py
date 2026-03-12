from __future__ import annotations

import logging

from nodalpath.engine.graph import TopologyGraph
from nodalpath.models.path import ComputedPath
from nodalpath.models.almanac import LabelBinding, IngressRule

log = logging.getLogger(__name__)

# SRGB range for NodalPath label allocation
# Uses the same range as IS-IS SR: base 16000
# Satellite SIDs: base + (plane * sats_per_plane + slot) + 1
# Ground station SIDs: gs_base + gs_index
def _srgb_base() -> int:
    from nodalpath.platform import get_nodalpath_config
    return get_nodalpath_config().satellite_sid_range_start

def _gs_sid_base() -> int:
    from nodalpath.platform import get_nodalpath_config
    return get_nodalpath_config().ground_station_sid_range_start


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


def path_to_label_stack(path: ComputedPath) -> list[int]:
    """Extract the MPLS label stack for a computed path.

    The label stack is the sequence of SIDs for nodes AFTER the ingress LER.
    The ingress LER pushes this stack. Each transit LSR pops the top label
    and forwards based on the next label. The penultimate hop pops the last
    label (PHP) and the egress node receives native IP.
    """
    return [hop.sid for hop in path.hops[1:]]


def build_lsr_bindings(
    node_id: str,
    paths: list[ComputedPath],
    graph: TopologyGraph,
) -> list[LabelBinding]:
    """Build MPLS LSR label bindings for a transit node.

    For each path that transits this node, create a LabelBinding:
    - in_label: this node's SID
    - action: "swap" if not penultimate hop, "pop" if penultimate hop
    - out_label: next hop's SID (None if action is "pop")
    - out_interface: the interface toward the next hop
    """
    bindings: list[LabelBinding] = []
    seen_labels: set[tuple[int, str]] = set()  # (in_label, path_id) dedup

    node_sid = graph.node_sids.get(node_id)
    if node_sid is None:
        return bindings

    for path in paths:
        # Find this node in the path's hops (not as first or last hop for transit)
        for i, hop in enumerate(path.hops):
            if hop.node_id != node_id:
                continue
            # Skip if this is the first hop (ingress LER) or last hop (egress)
            if i == 0 or i == len(path.hops) - 1:
                continue

            # This node is a transit node at position i
            in_label = hop.sid
            next_hop = path.hops[i + 1]
            out_interface = hop.out_interface

            # Dedup key
            dedup_key = (in_label, path.path_id)
            if dedup_key in seen_labels:
                log.warning(
                    "Duplicate in_label %d for node %s in path %s",
                    in_label, node_id, path.path_id,
                )
                continue
            seen_labels.add(dedup_key)

            # Penultimate hop: next hop is the last hop → action is "pop"
            is_penultimate = (i + 1 == len(path.hops) - 1)
            if is_penultimate:
                bindings.append(LabelBinding(
                    in_label=in_label,
                    action="pop",
                    out_label=None,
                    out_interface=out_interface or "",
                ))
            else:
                bindings.append(LabelBinding(
                    in_label=in_label,
                    action="swap",
                    out_label=next_hop.sid,
                    out_interface=out_interface or "",
                ))

    return bindings


def build_ler_ingress_rules(
    node_id: str,
    paths: list[ComputedPath],
    graph: TopologyGraph,
    prefix_map: dict[str, str],
) -> list[IngressRule]:
    """Build LER ingress rules for any node that is a path source.

    For each path where this node is the ingress (src_node_id == node_id),
    create an IngressRule:
    - dst_prefix: the destination node's advertised prefix
    - push_label: first label in the path's label_stack
    - out_interface: the interface toward the first-hop node
    """
    rules: list[IngressRule] = []
    for path in paths:
        if path.src_node_id != node_id:
            continue

        dst_prefix = prefix_map.get(path.dst_node_id)
        if dst_prefix is None:
            continue

        if not path.label_stack:
            continue

        push_label = path.label_stack[0]
        # out_interface is the first hop's out_interface (ingress LER's departing interface)
        out_interface = path.hops[0].out_interface or ""

        rules.append(IngressRule(
            dst_prefix=dst_prefix,
            push_label=push_label,
            out_interface=out_interface,
        ))

    return rules
