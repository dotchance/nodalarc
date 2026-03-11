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
        prefix_map:     node_id -> advertised prefix (ground stations only)
        node_registry:  node_id -> TopologyNode
        interface_map:  (node_a, node_b) -> (iface_a, iface_b) from session context

    Interface-to-peer wiring is derived from interface_map at construction time.
    Latency lookups are not available (would need TopologyEdge data); hops will
    have latency_to_next_ms=None until edge data is available.
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

        # Build (node_id, interface_name) -> peer_node_id from interface_map
        self._iface_to_peer: dict[tuple[str, str], str] = {}
        for (node_a, node_b), (iface_a, iface_b) in interface_map.items():
            self._iface_to_peer[(node_a, iface_a)] = node_b
            self._iface_to_peer[(node_b, iface_b)] = node_a

        # Build reverse prefix map: prefix -> node_id
        self._prefix_to_node: dict[str, str] = {
            prefix: node_id for node_id, prefix in prefix_map.items()
        }

    def derive(self, src: str, dst: str, sim_time: str | None = None) -> PathResult:
        """Derive the MPLS path from src to dst at the given sim_time.

        Args:
            src: Source node_id — must be a ground station with ler_ingress_rules
            dst: Destination node_id — must be a ground station with an advertised prefix
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
        """Derive path using a specific AlmanacEntry."""
        from nodalpath.models.almanac import ForwardingTable

        # Build node_id -> ForwardingTable lookup for this entry
        ft_by_node: dict[str, ForwardingTable] = {
            ft.node_id: ft for ft in entry.forwarding_tables
        }

        # Validate src and dst are ground stations
        src_node = self._node_registry.get(src)
        dst_node = self._node_registry.get(dst)

        if src_node is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"src node '{src}' not in registry")
        if dst_node is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"dst node '{dst}' not in registry")
        if src_node.node_type != "ground_station":
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"src '{src}' is not a ground station")
        if dst_node.node_type != "ground_station":
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"dst '{dst}' is not a ground station")

        # Find the dst prefix
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

        # Build path starting with src ground station
        hops: list[PathHop] = []
        current_node = src
        current_label = ingress_rule.push_label
        current_iface = ingress_rule.out_interface
        visited: set[str] = {src}

        # Src hop
        peer = self._iface_to_peer.get((src, current_iface))
        latency: float | None = None  # no edge data available from interface_map

        hops.append(PathHop(
            node_id=src,
            node_type="ground_station",
            in_label=None,
            out_label=current_label,
            action="push",
            out_interface=current_iface,
            latency_to_next_ms=latency,
        ))

        if peer is None:
            return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                     f"interface '{current_iface}' on '{src}' not in interface_map")

        current_node = peer

        # Step 2: Transit — follow LSR bindings
        for _ in range(MAX_HOPS):
            if current_node in visited:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"routing loop detected at '{current_node}'")
            visited.add(current_node)

            node = self._node_registry.get(current_node)
            node_type = node.node_type if node else "satellite"

            # Check if this is the destination ground station
            if current_node == dst:
                hops.append(PathHop(
                    node_id=current_node,
                    node_type="ground_station",
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

            binding = None
            for b in ft.lsr_bindings:
                if b.in_label == current_label:
                    binding = b
                    break

            if binding is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"no LSR binding for label {current_label} at '{current_node}'")

            # Find peer for this hop
            peer = self._iface_to_peer.get((current_node, binding.out_interface))

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
                # Next node should be the dst ground station
                if peer is not None:
                    next_node = self._node_registry.get(peer)
                    if next_node is not None:
                        hops.append(PathHop(
                            node_id=peer,
                            node_type=next_node.node_type,
                            in_label=None,
                            out_label=None,
                            action=None,
                            out_interface=None,
                            latency_to_next_ms=None,
                        ))
                break

            if peer is None:
                return self._unreachable(src, dst, entry.sim_time, entry.topology_state_id,
                                         f"interface '{binding.out_interface}' on '{current_node}' not in interface_map")

            current_node = peer
            current_label = binding.out_label if binding.out_label is not None else current_label

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
