# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Test doubles for the resolved OME runtime contract."""

from collections.abc import Iterator, Mapping


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
