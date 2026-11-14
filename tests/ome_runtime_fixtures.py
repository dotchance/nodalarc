# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Test doubles for the resolved OME runtime contract."""

from collections.abc import Iterator, Mapping, Sequence, Set

from ome.event_stream import ground_terminal_pools, ground_usable_pairs
from ome.link_allocator import LinkAllocationResult, allocate_links
from ome.visibility import GroundVisibility


class StaticOmeAddressing:
    """Explicit runtime node registry for isolated OME algorithm tests."""

    def __init__(
        self,
        *,
        satellite_ids: tuple[str, ...] = (),
        ground_station_ids: tuple[str, ...] = (),
        ground_aliases: dict[str, str] | None = None,
    ) -> None:
        self._node_types = {
            **dict.fromkeys(satellite_ids, "satellite"),
            **dict.fromkeys(ground_station_ids, "ground_station"),
        }
        self._ground_aliases = dict(ground_aliases or {})

    @property
    def has_type_registry(self) -> bool:
        return bool(self._node_types)

    def node_type(self, node_id: str) -> str:
        try:
            return self._node_types[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown test runtime node_id {node_id!r}") from exc

    def is_ground_segment(self, node_id: str) -> bool:
        return self.node_type(node_id) == "ground_station"

    def is_satellite(self, node_id: str) -> bool:
        return self.node_type(node_id) == "satellite"

    def sat_id(self, plane: int, slot: int) -> str:
        raise KeyError(
            f"test runtime has no plane/slot identity for ({plane}, {slot}); use node_id"
        )

    def gs_id(self, name: str) -> str:
        try:
            return self._ground_aliases[name]
        except KeyError as exc:
            if name in self._node_types and self._node_types[name] == "ground_station":
                return name
            raise KeyError(f"unknown test ground station alias {name!r}") from exc


class UniformPairDelays(Mapping[tuple[str, str], tuple[float, float]]):
    """The same one-way delay in both directions for every pair: a stand-in for the tick's
    ground decisions in allocator tests whose links are short (LEO-like), where no test reads a
    particular pair's delay."""

    def __init__(self, delay_ms: float) -> None:
        self._delays = (delay_ms, delay_ms)

    def __getitem__(self, pair: tuple[str, str]) -> tuple[float, float]:
        return self._delays

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(())

    def __len__(self) -> int:
        return 0


def allocator_timing_kwargs() -> dict:
    """The allocator's per-tick timing inputs for tests that do not exercise reception windows:
    a fixed tick time, 3 ms each way on every pair, and no reception carried from earlier
    ticks."""
    return {
        "sim_time_unix": 1000.0,
        "pair_delays_ms": UniformPairDelays(3.0),
        "receive_windows": {},
    }


def allocate_ground_scenario(
    *,
    visible_per_station: Mapping[str, Sequence[GroundVisibility]],
    ground_station_ids: Set[str],
    gs_terminal_indices: Mapping[str, Sequence[int]],
    gs_selection_policies: Mapping,
    gs_min_elevations: Mapping[str, float],
    gs_handover_policies: Mapping,
    gs_handover_modes: Mapping[str, str],
    gs_mbb_overlap_ticks: Mapping[str, int],
    gs_mbb_reserve: Mapping[str, int],
    gs_service_priorities: Mapping[str, int],
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    sat_ground_terminal_indices_by_body: Mapping[str, Mapping[str, Sequence[int]]],
    **allocator_inputs,
) -> LinkAllocationResult:
    """Run the link allocator on a ground scenario: stations that state scheduling, and the
    satellites that serve them from one terminal pool per target body.

    The scenario reaches the allocator through the same two adapters OME's step uses, so
    these tests cover the path a ground link takes in production.
    """
    return allocate_links(
        usable=ground_usable_pairs(
            visible_per_station=visible_per_station,
            gs_reference_bodies=gs_reference_bodies,
            # Every station of the scenario states scheduling, and the scenario names no
            # link rule.
            stating_nodes=set(ground_station_ids) | set(gs_terminal_indices),
            pair_pools=None,
        ),
        terminal_pools=ground_terminal_pools(
            gs_terminal_indices=gs_terminal_indices,
            sat_terminal_indices_by_body=sat_ground_terminal_indices_by_body,
        ),
        stating_nodes=set(ground_station_ids) | set(gs_terminal_indices),
        rule_n={},
        selection_policies=gs_selection_policies,
        min_elevations=gs_min_elevations,
        handover_policies=gs_handover_policies,
        handover_modes=gs_handover_modes,
        mbb_overlap_ticks=gs_mbb_overlap_ticks,
        mbb_reserve=gs_mbb_reserve,
        service_priorities=gs_service_priorities,
        tenant_ids=gs_tenant_ids,
        reference_bodies=gs_reference_bodies,
        **allocator_inputs,
    )
