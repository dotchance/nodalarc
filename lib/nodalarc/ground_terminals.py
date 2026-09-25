# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground terminal helpers.

Ground station terminal definitions describe groups of identical terminals.
Runtime allocation capacity is the resolver-owned Linux interface pool; a
terminal capability cannot create additional interfaces implicitly. Keeping
pool validation and terminal-physics-profile selection in one shared helper
prevents OME and Scheduler from silently disagreeing about usable mounts.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.models.terminal_physics import (
    SatGroundTerminalBoresight,
    TerminalBoresight,
)
from nodalarc.ome_runtime import GroundStation, GroundStationFile


class GroundTerminalTypeLike(Protocol):
    type: str


class GroundTerminalCapacityLike(GroundTerminalTypeLike, Protocol):
    count: int
    interface_indices: tuple[int, ...]


class TerminalPhysicsLike(GroundTerminalTypeLike, Protocol):
    count: int
    interface_indices: tuple[int, ...]
    max_range_km: float
    field_of_regard_deg: float
    max_tracking_rate_deg_s: float
    boresight: TerminalBoresight | SatGroundTerminalBoresight


@dataclass(frozen=True, slots=True)
class TerminalPhysicsProfile:
    """Effective physical constraints for a terminal collection.

    Until allocation is terminal-block-aware, a collection with multiple
    different physics signatures cannot be collapsed honestly. The helper
    below fails loud in that case instead of mixing max range from one block
    with FoR from another.
    """

    profile_id: str
    max_range_km: float
    field_of_regard_deg: float
    max_tracking_rate_deg_s: float
    boresight: TerminalBoresight | SatGroundTerminalBoresight
    target_body: SupportedSurfaceBody | None = None

    def __post_init__(self) -> None:
        if isinstance(self.boresight, SatGroundTerminalBoresight):
            if self.target_body != self.boresight.target_body:
                raise ValueError(
                    "TerminalPhysicsProfile target_body must match satellite boresight "
                    f"target_body={self.boresight.target_body!r}; got {self.target_body!r}"
                )
        elif self.target_body is not None:
            raise ValueError(
                "TerminalPhysicsProfile target_body is only valid for satellite "
                "ground-terminal boresights"
            )


def ground_terminal_capacity(terminals: Iterable[GroundTerminalCapacityLike]) -> int:
    """Return allocatable interface capacity for terminal blocks."""
    return len(ground_terminal_interface_indices(tuple(terminals)))


def ground_terminal_interface_indices(
    terminals: Sequence[GroundTerminalCapacityLike],
) -> tuple[int, ...]:
    """Return the global ground-interface pool carried by terminal blocks.

    Runtime inputs carry explicit global indices; allocation never recreates
    or renumbers them from block order.
    """
    if not terminals:
        raise ValueError("ground terminal interface pool requires at least one terminal")
    indices = tuple(index for term in terminals for index in term.interface_indices)
    expected = sum(int(term.count) for term in terminals)
    if len(indices) != expected:
        raise ValueError(
            "ground terminal interface pool does not match expanded terminal count: "
            f"count={expected}, indices={indices}"
        )
    if len(set(indices)) != len(indices):
        raise ValueError(f"ground terminal interface pool contains duplicate indices: {indices}")
    if any(index < 0 for index in indices):
        raise ValueError(f"ground terminal interface pool contains negative indices: {indices}")
    return indices


def station_ground_terminal_capacity(
    gs_file: GroundStationFile,
    station: GroundStation,
) -> int:
    """Return a station's effective ground terminal capacity.

    Per-station terminal definitions override the file defaults. A missing
    terminal list is a configuration error; callers must not invent term0.
    """
    terminals = station.terminals or gs_file.default_terminals
    if not terminals:
        raise ValueError(f"Ground station {station.name!r} has no terminal definitions")
    return ground_terminal_capacity(terminals)


def ground_terminal_type(terminals: Iterable[GroundTerminalTypeLike]) -> str:
    """Return the single terminal type represented by a terminal collection.

    Until the allocator carries terminal-block identity, mixed RF/optical
    ground terminal sets cannot be represented truthfully as one event field.
    Fail loudly instead of publishing a guessed terminal type.
    """
    terminal_list = list(terminals)
    if not terminal_list:
        raise ValueError("ground terminal type requires at least one terminal")
    types = {str(term.type) for term in terminal_list}
    if len(types) != 1:
        raise ValueError(
            "mixed ground terminal types require terminal-block-aware allocation; "
            f"got {sorted(types)}"
        )
    return next(iter(types))


def satellite_terminal_index_pools_by_target_body(
    terminals: Sequence[TerminalPhysicsLike],
    *,
    total_count: int,
) -> dict[str, tuple[int, ...]]:
    """Return allocatable satellite ground-terminal indices per target body.

    Each block carries resolver-owned global ``gndN`` indices and a satellite
    boresight naming its target body; the allocator may only assign indices
    from the pool matching the ground station's reference body.
    """

    if total_count < 0:
        raise ValueError(f"satellite ground terminal count must be >= 0, got {total_count}")
    if total_count == 0:
        return {}

    pools: dict[str, list[int]] = {}
    if not terminals:
        raise ValueError(
            "ground allocation requires satellite ground terminal definitions with "
            "explicit interface_indices"
        )

    next_index = 0
    for block_idx, term in enumerate(terminals):
        count = int(term.count)
        if count <= 0:
            raise ValueError(f"satellite ground terminal block {block_idx} count must be positive")
        indices = tuple(term.interface_indices)
        if len(indices) != count:
            raise ValueError(
                f"satellite ground terminal block {block_idx} count={count} does not match "
                f"interface_indices={indices}"
            )
        next_index += count

        boresight = term.boresight
        if not isinstance(boresight, SatGroundTerminalBoresight):
            raise ValueError(
                f"satellite ground terminal block {block_idx} carries a ground boresight"
            )
        pools.setdefault(str(boresight.target_body), []).extend(indices)

    if next_index != total_count:
        raise ValueError(
            "satellite ground terminal count does not match expanded terminal blocks: "
            f"ground_terminal_count={total_count}, expanded={next_index}"
        )

    all_indices = {index for indices in pools.values() for index in indices}
    if len(all_indices) != total_count:
        raise ValueError(
            "satellite ground terminal pools do not cover the selected interface capacity: "
            f"ground_terminal_count={total_count}, indices={sorted(all_indices)}"
        )
    return {body: tuple(indices) for body, indices in sorted(pools.items())}


def station_ground_terminal_type(
    gs_file: GroundStationFile,
    station: GroundStation,
) -> str:
    """Return the effective terminal type for a ground station."""
    terminals = station.terminals or gs_file.default_terminals
    return ground_terminal_type(terminals)


def terminal_physics_profile(
    terminals: Sequence[TerminalPhysicsLike],
    *,
    profile_id: str,
    endpoint: Literal["ground", "satellite"],
) -> TerminalPhysicsProfile:
    """Collapse a terminal collection into the one profile OME can apply today."""
    if not terminals:
        raise ValueError(f"{profile_id} has no terminal definitions")

    signatures: set[tuple[float, float, float, str]] = set()
    for term in terminals:
        boresight = _validated_boresight(term.boresight, endpoint=endpoint, profile_id=profile_id)
        signatures.add(
            (
                float(term.max_range_km),
                float(term.field_of_regard_deg),
                float(term.max_tracking_rate_deg_s),
                boresight.model_dump_json(),
            )
        )
    if len(signatures) != 1:
        raise ValueError(
            f"{profile_id} has heterogeneous ground terminal physics. "
            "Terminal-block-aware allocation is required before these can be "
            "collapsed into one visibility decision."
        )

    term = terminals[0]
    boresight = _validated_boresight(term.boresight, endpoint=endpoint, profile_id=profile_id)
    target_body = (
        boresight.target_body if isinstance(boresight, SatGroundTerminalBoresight) else None
    )
    return TerminalPhysicsProfile(
        profile_id=profile_id,
        max_range_km=float(term.max_range_km),
        field_of_regard_deg=float(term.field_of_regard_deg),
        max_tracking_rate_deg_s=float(term.max_tracking_rate_deg_s),
        boresight=boresight,
        target_body=target_body,
    )


def terminal_physics_profiles(
    terminals: Sequence[TerminalPhysicsLike],
    *,
    profile_id: str,
    endpoint: Literal["ground", "satellite"],
) -> tuple[TerminalPhysicsProfile, ...]:
    """Return one visibility profile per target-body-compatible terminal block.

    Satellite ground terminals may legitimately target different bodies (for
    example one nadir antenna for Earth and one for Luna). Collapsing that
    collection into a single profile rejects a valid cislunar relay shape, so
    this helper keeps target-body-distinct profiles separate while still
    refusing heterogeneous physics for the same target body.
    """
    if not terminals:
        raise ValueError(f"{profile_id} has no terminal definitions")

    profiles_by_target: dict[str | None, TerminalPhysicsProfile] = {}
    signatures_by_target: dict[str | None, tuple[float, float, float, str]] = {}
    for idx, term in enumerate(terminals):
        boresight = _validated_boresight(term.boresight, endpoint=endpoint, profile_id=profile_id)
        target_body = (
            boresight.target_body if isinstance(boresight, SatGroundTerminalBoresight) else None
        )
        signature = (
            float(term.max_range_km),
            float(term.field_of_regard_deg),
            float(term.max_tracking_rate_deg_s),
            boresight.model_dump_json(),
        )
        existing = signatures_by_target.get(target_body)
        if existing is not None and existing != signature:
            target_label = f" target_body={target_body!r}" if target_body is not None else ""
            raise ValueError(
                f"{profile_id}{target_label} has heterogeneous ground terminal physics. "
                "Terminal-block-aware allocation is required before these can be "
                "collapsed into one visibility decision."
            )
        signatures_by_target[target_body] = signature
        if target_body not in profiles_by_target:
            block_profile_id = profile_id if len(terminals) == 1 else f"{profile_id}[{idx}]"
            profiles_by_target[target_body] = TerminalPhysicsProfile(
                profile_id=block_profile_id,
                max_range_km=float(term.max_range_km),
                field_of_regard_deg=float(term.field_of_regard_deg),
                max_tracking_rate_deg_s=float(term.max_tracking_rate_deg_s),
                boresight=boresight,
                target_body=target_body,
            )

    return tuple(profiles_by_target.values())


def _validated_boresight(
    boresight: TerminalBoresight | SatGroundTerminalBoresight,
    *,
    endpoint: Literal["ground", "satellite"],
    profile_id: str,
) -> TerminalBoresight | SatGroundTerminalBoresight:
    if endpoint == "ground" and not isinstance(boresight, TerminalBoresight):
        raise ValueError(f"{profile_id} must use a ground TerminalBoresight")
    if endpoint == "satellite" and not isinstance(boresight, SatGroundTerminalBoresight):
        raise ValueError(f"{profile_id} must use a satellite ground-terminal boresight")
    return boresight
