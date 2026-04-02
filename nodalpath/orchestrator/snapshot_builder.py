"""Snapshot builder — stateful topology builder from timeline events.

Maintains active links and node positions, produces TopologySnapshot
instances on demand.
"""

from __future__ import annotations

from nodalarc.geo import compute_latency_ms, geodetic_to_ecef
from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent

from nodalpath.models.topology import TopologyEdge, TopologyNode, TopologySnapshot


class SnapshotBuilder:
    """Accumulates timeline events and produces TopologySnapshot instances.

    Tracks active links (with range_km) and ECEF positions for all nodes.
    """

    def __init__(
        self,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float] | None = None,
        static_edges: list[TopologyEdge] | None = None,
    ) -> None:
        self._node_registry = node_registry
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map or {}
        self._static_edges: list[TopologyEdge] = static_edges or []
        self._active_links: dict[tuple[str, str], float] = {}  # canonical pair -> range_km
        self._all_links: dict[tuple[str, str], tuple[bool, bool, float]] = {}
        self._positions: dict[str, tuple[float, float, float]] = {}  # node_id -> ECEF

        # Pre-populate _all_links for static terrestrial pairs (always visible/scheduled)
        for edge in self._static_edges:
            pair = (edge.src_node_id, edge.dst_node_id)
            self._all_links[pair] = (True, True, 0.0)

    def apply_position_record(self, record: TimelinePositionSnapshot) -> None:
        """Update ECEF positions from a TimelinePositionSnapshot."""
        for node_id, pos in record.positions.items():
            self._positions[node_id] = geodetic_to_ecef(
                pos.lat_deg,
                pos.lon_deg,
                pos.alt_km,
            )

    def apply_link_event(self, event: VisibilityEvent) -> None:
        """Add or remove a link based on VisibilityEvent state.

        Link UP: visible=True AND scheduled=True.
        Anything else: link DOWN.
        Canonical pair (node_a, node_b) is already alphabetically ordered.
        """
        pair = (event.node_a, event.node_b)
        if event.visible and event.scheduled:
            self._active_links[pair] = event.range_km
        else:
            self._active_links.pop(pair, None)

        self._all_links[pair] = (event.visible, event.scheduled, event.range_km)

    @property
    def full_link_state(self) -> dict[tuple[str, str], tuple[bool, bool, float]]:
        """Snapshot of full link visibility state at current sim_time."""
        return dict(self._all_links)

    @property
    def active_link_set(self) -> frozenset[tuple[str, str]]:
        """Current set of active link pairs for transition diffing."""
        return frozenset(self._active_links.keys())

    def build_snapshot(self, sim_time: str) -> TopologySnapshot:
        """Produce a TopologySnapshot from current state.

        Includes all nodes from the registry and edges for active links only.
        """
        nodes = list(self._node_registry.values())
        edges: list[TopologyEdge] = list(self._static_edges)

        for pair, range_km in self._active_links.items():
            a, b = pair
            ifaces = self._interface_map.get(pair, ("unknown", "unknown"))
            latency_ms = compute_latency_ms(range_km)
            bandwidth = self._bandwidth_map.get(pair, 1000.0)
            link_type = "ground" if a.startswith("gs-") or b.startswith("gs-") else "isl"

            edges.append(
                TopologyEdge(
                    src_node_id=a,
                    dst_node_id=b,
                    src_interface=ifaces[0],
                    dst_interface=ifaces[1],
                    latency_ms=latency_ms,
                    bandwidth_mbps=bandwidth,
                    link_type=link_type,
                )
            )

        return TopologySnapshot(sim_time=sim_time, nodes=nodes, edges=edges)
