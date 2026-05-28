# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground terminal helpers.

Ground station terminal definitions describe groups of identical terminals.
The emulation capacity is therefore `count * tracking_capacity` for each
terminal block. Keeping this arithmetic and the Phase 2 physics-profile
selection in one shared helper prevents the OME, Scheduler, Operator, and
template renderer from silently disagreeing about terminal capabilities.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from nodalarc.body_frames import SUPPORTED_BODY_NAMES, SupportedSurfaceBody
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
)
from nodalarc.models.terminal_physics import (
    SatGroundTerminalBoresight,
    TerminalBoresight,
)


class GroundTerminalTypeLike(Protocol):
    type: str


class GroundTerminalCapacityLike(GroundTerminalTypeLike, Protocol):
    count: int
    tracking_capacity: int


class TerminalPhysicsLike(GroundTerminalTypeLike, Protocol):
    max_range_km: float | None
    field_of_regard_deg: float | None
    max_tracking_rate_deg_s: float | None
    boresight: TerminalBoresight | SatGroundTerminalBoresight | None


@dataclass(frozen=True, slots=True)
class TerminalPhysicsProfile:
    """Effective physical constraints for a terminal collection.

    Until allocation is terminal-block-aware, a collection with multiple
    different physics signatures cannot be collapsed honestly. The helper
    below fails loud in that case instead of mixing max range from one block
    with FoR from another.
    """

    profile_id: str | None
    max_range_km: float | None
    field_of_regard_deg: float | None
    max_tracking_rate_deg_s: float | None
    boresight: TerminalBoresight | SatGroundTerminalBoresight | None
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
    """Return total simultaneous satellite links supported by terminal blocks."""
    total = sum(int(term.count) * int(term.tracking_capacity) for term in terminals)
    if total <= 0:
        raise ValueError("ground terminal capacity must be positive")
    return total


def station_ground_terminal_capacity(
    gs_file: GroundStationFile,
    station: GroundStationConfig,
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
    ground_link_model: Literal["geometry_only", "terminal_physics"],
) -> dict[str, tuple[int, ...]]:
    """Return allocatable satellite ground-terminal indices per target body.

    Satellite ground terminals are expanded in YAML order: a block with
    ``count: 2`` owns two consecutive terminal indices. In terminal_physics
    mode, each block must declare a satellite boresight target_body; the
    allocator may only assign indices from the pool matching the ground
    station's reference body. In geometry_only mode, terminal boresight
    constraints are intentionally absent, so each index is eligible for every
    supported surface body while still consuming one global physical terminal.
    """

    if total_count < 0:
        raise ValueError(f"satellite ground terminal count must be >= 0, got {total_count}")
    if total_count == 0:
        return {}

    pools: dict[str, list[int]] = {}
    if not terminals:
        if ground_link_model == "terminal_physics":
            raise ValueError(
                "terminal_physics ground allocation requires satellite ground terminal "
                "definitions with boresight.target_body"
            )
        all_indices = tuple(range(total_count))
        return dict.fromkeys(SUPPORTED_BODY_NAMES, all_indices)

    next_index = 0
    for block_idx, term in enumerate(terminals):
        count = int(term.count)
        if count <= 0:
            raise ValueError(f"satellite ground terminal block {block_idx} count must be positive")
        indices = tuple(range(next_index, next_index + count))
        next_index += count

        boresight = term.boresight
        if isinstance(boresight, SatGroundTerminalBoresight):
            targets = (boresight.target_body,)
        elif ground_link_model == "geometry_only":
            targets = SUPPORTED_BODY_NAMES
        else:
            raise ValueError(
                f"satellite ground terminal block {block_idx} is missing "
                "boresight.target_body; terminal_physics allocation cannot assign "
                "body-specific terminal indices"
            )

        for target_body in targets:
            pools.setdefault(str(target_body), []).extend(indices)

    if next_index != total_count:
        raise ValueError(
            "satellite ground terminal count does not match expanded terminal blocks: "
            f"ground_terminal_count={total_count}, expanded={next_index}"
        )

    return {body: tuple(indices) for body, indices in sorted(pools.items())}


def station_ground_terminal_type(
    gs_file: GroundStationFile,
    station: GroundStationConfig,
) -> str:
    """Return the effective terminal type for a ground station."""
    terminals = station.terminals or gs_file.default_terminals
    return ground_terminal_type(terminals)


def terminal_physics_missing_fields(term: TerminalPhysicsLike) -> tuple[str, ...]:
    """Return Phase 2 terminal_physics fields missing from a terminal definition."""
    missing: list[str] = []
    if term.max_range_km is None:
        missing.append("max_range_km")
    if term.field_of_regard_deg is None:
        missing.append("field_of_regard_deg")
    if term.max_tracking_rate_deg_s is None:
        missing.append("max_tracking_rate_deg_s")
    if term.boresight is None:
        missing.append("boresight")
    return tuple(missing)


def terminal_collection_missing_physics(
    terminals: Sequence[TerminalPhysicsLike],
    *,
    label: str,
) -> tuple[str, ...]:
    """Return human-readable missing-physics errors for a terminal collection."""
    errors: list[str] = []
    if not terminals:
        return (f"{label}: no terminal definitions",)
    for idx, term in enumerate(terminals):
        missing = terminal_physics_missing_fields(term)
        if missing:
            errors.append(f"{label}[{idx}] missing {', '.join(missing)}")
    return tuple(errors)


def terminal_physics_profile(
    terminals: Sequence[TerminalPhysicsLike],
    *,
    profile_id: str,
    endpoint: Literal["ground", "satellite"],
    require_constraints: bool,
) -> TerminalPhysicsProfile:
    """Collapse a terminal collection into the one profile OME can apply today."""
    if not terminals:
        raise ValueError(f"{profile_id} has no terminal definitions")

    missing_errors = terminal_collection_missing_physics(terminals, label=profile_id)
    if require_constraints and missing_errors:
        raise ValueError(
            "terminal_physics ground visibility requires terminal physics fields: "
            + "; ".join(missing_errors)
        )
    if missing_errors:
        return TerminalPhysicsProfile(
            profile_id=None,
            max_range_km=None,
            field_of_regard_deg=None,
            max_tracking_rate_deg_s=None,
            boresight=None,
            target_body=None,
        )

    signatures: set[tuple[float, float, float, str]] = set()
    for term in terminals:
        if (
            term.max_range_km is None
            or term.field_of_regard_deg is None
            or term.max_tracking_rate_deg_s is None
            or term.boresight is None
        ):
            raise ValueError(f"{profile_id} has incomplete terminal physics")
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
    if (
        term.max_range_km is None
        or term.field_of_regard_deg is None
        or term.max_tracking_rate_deg_s is None
        or term.boresight is None
    ):
        raise ValueError(f"{profile_id} has incomplete terminal physics")
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
    require_constraints: bool,
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

    missing_errors = terminal_collection_missing_physics(terminals, label=profile_id)
    if require_constraints and missing_errors:
        raise ValueError(
            "terminal_physics ground visibility requires terminal physics fields: "
            + "; ".join(missing_errors)
        )
    if missing_errors:
        return (
            TerminalPhysicsProfile(
                profile_id=None,
                max_range_km=None,
                field_of_regard_deg=None,
                max_tracking_rate_deg_s=None,
                boresight=None,
                target_body=None,
            ),
        )

    profiles_by_target: dict[str | None, TerminalPhysicsProfile] = {}
    signatures_by_target: dict[str | None, tuple[float, float, float, str]] = {}
    for idx, term in enumerate(terminals):
        if (
            term.max_range_km is None
            or term.field_of_regard_deg is None
            or term.max_tracking_rate_deg_s is None
            or term.boresight is None
        ):
            raise ValueError(f"{profile_id} has incomplete terminal physics")
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
