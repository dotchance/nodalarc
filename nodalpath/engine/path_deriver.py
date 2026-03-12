"""Path deriver — reconstructs MPLS forwarding paths from computed almanac entries.

Walks ForwardingTable.ler_ingress_rules at the source ground station to find
the initial label push, then follows lsr_bindings hop by hop until the path
reaches the destination ground station.

Does not inject probes. Does not require a running network. Derives paths
purely from the forwarding tables computed by compute_almanac_entry().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nodalarc.models.path import PathHop, PathResult

if TYPE_CHECKING:
    from nodalpath.orchestrator.almanac_store import AlmanacStore
    from nodalpath.models.almanac import AlmanacEntry

log = logging.getLogger(__name__)

# Maximum hops before declaring a routing loop
MAX_HOPS = 64


class PathDeriver:
    """Derives MPLS forwarding paths from almanac entries.

    Requires:
        almanac_store:  Source of ForwardingTable entries per topology state
        prefix_map:     node_id -> advertised prefix (all nodes)
        node_registry:  node_id -> TopologyNode
        interface_map:  (node_a, node_b) -> (iface_a, iface_b) from session context

    Path traversal uses SID-to-node lookup (labels encode node identity)
    rather than interface-to-peer mapping, because ground station links
    share the "gnd0" interface across multiple peers.
    """

    def __init__(
        self,
        almanac_store: AlmanacStore,
        prefix_map: dict[str, str],
        node_registry: dict,
        interface_map: dict[tuple[str, str], tuple[str, str]],
    ) -> None:
        self._almanac_store = almanac_store
        self._prefix_map = prefix_map
        self._node_registry = node_registry

        # Build SID -> node_id for label-based traversal
        self._sid_to_node: dict[int, str] = {}
        for node_id, node in node_registry.items():
            self._sid_to_node[node.sid] = node_id

        # Build reverse prefix map: prefix -> node_id
        self._prefix_to_node: dict[str, str] = {
            prefix: node_id for node_id, prefix in prefix_map.items()
        }

    def derive(self, src: str, dst: str, sim_time: str | None = None) -> PathResult:
        """Derive the MPLS path from src to dst at the given sim_time.

        Args:
            src: Source node_id (typically a ground station with ler_ingress_rules,
                 but any node in the registry is accepted)
            dst: Destination node_id (must have an advertised prefix in prefix_map)
            sim_time: ISO 8601 sim_time to query. None = most recent entry.

        Returns:
            PathResult with reachable=True and hops list, or reachable=False
            with unreachable_reason explaining why.
        """
        if sim_time is None:
            entries = self._almanac_store.entries
            if not entries:
                return self._unreachable(src, dst, "", "", "no almanac entries available")
            entry = entries[-1]
        else:
            entry = self._almanac_store.get_entry_at(sim_time)
            if entry is None:
                return self._unreachable(src, dst, sim_time or "", "", "no almanac entry at requested sim_time")

        return self._derive_from_entry(src, dst, entry)

    def _derive_from_entry(self, src: str, dst: str, entry: AlmanacEntry) -> PathResult:
        """Derive path using a specific AlmanacEntry.

        Traversal uses SID-based lookup: the push_label at ingress and the
        out_label at each transit hop encode the next node's SID, so we
        resolve the next node via _sid_to_node rather than interface wiring.
        This avoids the ambiguity of gnd0 interfaces that connect GS nodes
        to multiple satellites.
        """
        from nodalpath.models.almanac import ForwardingTable

        # Build node_id -> ForwardingTable lookup for this entry
        ft_by_node: dict[str, ForwardingTable] = {
            ft.node_id: ft for ft in entry.forwarding_tables
        }

        # Validate src and dst
        src_node = self._node_registry.get(src)
        dst_node = self._node_registry.get(dst)

        if src_node is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"src node '{src}' not in registry")
        if dst_node is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"dst node '{dst}' not in registry")

        # Find the dst prefix — only ground stations have advertised prefixes
        dst_prefix = self._prefix_map.get(dst)
        if dst_prefix is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"dst '{dst}' has no advertised prefix in prefix_map")

        # Step 1: Ingress — find the LER rule at src for dst_prefix
        src_ft = ft_by_node.get(src)
        if src_ft is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"no forwarding table for src '{src}'")

        ingress_rule = None
        for rule in src_ft.ler_ingress_rules:
            if rule.dst_prefix == dst_prefix:
                ingress_rule = rule
                break

        if ingress_rule is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"no ingress rule for dst_prefix '{dst_prefix}' at src '{src}'")

        # Resolve the first-hop node via SID encoded in push_label
        first_hop = self._sid_to_node.get(ingress_rule.push_label)
        if first_hop is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"push_label {ingress_rule.push_label} does not match any node SID")

        # Build path starting with src node
        hops: list[PathHop] = []
        current_label = ingress_rule.push_label
        visited: set[str] = {src}

        hops.append(PathHop(
            node_id=src,
            node_type=src_node.node_type,
            in_label=None,
            out_label=current_label,
            action="push",
            out_interface=ingress_rule.out_interface,
            latency_to_next_ms=None,
        ))

        current_node = first_hop

        # Step 2: Transit — follow LSR bindings using SID-based next-hop resolution
        for _ in range(MAX_HOPS):
            if current_node in visited:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"routing loop detected at '{current_node}'")
            visited.add(current_node)

            node = self._node_registry.get(current_node)
            node_type = node.node_type if node else "satellite"

            # Check if this is the destination
            if current_node == dst:
                hops.append(PathHop(
                    node_id=current_node,
                    node_type=node_type,
                    in_label=current_label,
                    out_label=None,
                    action="pop",
                    out_interface=None,
                    latency_to_next_ms=None,
                ))
                break

            # Find LSR binding for current label
            ft = ft_by_node.get(current_node)
            if ft is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"no forwarding table for transit node '{current_node}'")

            # Collect all matching bindings for the current label.
            # Multiple paths through the same node produce multiple bindings
            # with the same in_label (the node's SID) but different actions
            # and out_interfaces.  Pick the one consistent with this path's
            # direction of travel:
            #   - "pop" is correct only when the egress interface leads to dst
            #   - otherwise prefer "swap" toward the destination
            candidates = [b for b in ft.lsr_bindings if b.in_label == current_label]

            binding = None
            if len(candidates) == 1:
                binding = candidates[0]
            elif len(candidates) > 1:
                # Prefer the binding whose out_label resolves to a node
                # we haven't visited (forward progress).  A "pop" binding
                # is correct only when the peer on that interface is the
                # destination; otherwise pick a "swap" that advances toward dst.
                for b in candidates:
                    if b.action == "pop":
                        # Check if this pop leads to dst — impossible to
                        # know solely from forwarding tables, so defer pops
                        # unless it's the only option.
                        continue
                    if b.out_label is not None:
                        next_id = self._sid_to_node.get(b.out_label)
                        if next_id is not None and next_id not in visited:
                            binding = b
                            break
                # Fall back to pop if no swap candidate found
                if binding is None:
                    for b in candidates:
                        if b.action == "pop":
                            binding = b
                            break
                # Last resort: first candidate
                if binding is None:
                    binding = candidates[0]

            if binding is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"no LSR binding for label {current_label} at '{current_node}'")

            hops.append(PathHop(
                node_id=current_node,
                node_type=node_type,
                in_label=current_label,
                out_label=binding.out_label,
                action=binding.action,
                out_interface=binding.out_interface,
                latency_to_next_ms=None,
            ))

            if binding.action == "pop":
                # Penultimate hop pops — next node is the egress (dst).
                # Resolve via out_label if present, otherwise the peer
                # on this interface is the destination.
                egress_node_id = dst  # PHP implies next is dst
                egress_node = self._node_registry.get(egress_node_id)
                if egress_node is not None:
                    hops.append(PathHop(
                        node_id=egress_node_id,
                        node_type=egress_node.node_type,
                        in_label=None,
                        out_label=None,
                        action=None,
                        out_interface=None,
                        latency_to_next_ms=None,
                    ))
                break

            # Resolve next hop via out_label (which is the next node's SID)
            if binding.out_label is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"swap binding at '{current_node}' has no out_label")

            next_node = self._sid_to_node.get(binding.out_label)
            if next_node is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"out_label {binding.out_label} does not match any node SID")

            current_node = next_node
            current_label = binding.out_label

        else:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"path exceeded MAX_HOPS ({MAX_HOPS})")

        total_latency = sum(
            h.latency_to_next_ms for h in hops if h.latency_to_next_ms is not None
        )

        return PathResult(
            src=src,
            dst=dst,
            hops=hops,
            total_latency_ms=total_latency,
            method="derived",
            sim_time=entry.sim_time,
            topology_state_id=entry.topology_state_id,
            reachable=True,
        )

    @staticmethod
    def _unreachable(src, dst, sim_time, state_id, reason) -> PathResult:
        return PathResult(
            src=src,
            dst=dst,
            hops=[],
            total_latency_ms=0.0,
            method="derived",
            sim_time=sim_time,
            topology_state_id=state_id,
            reachable=False,
            unreachable_reason=reason,
        )
