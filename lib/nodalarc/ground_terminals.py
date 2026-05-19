# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground terminal capacity helpers.

Ground station terminal definitions describe groups of identical terminals.
The emulation capacity is therefore `count * tracking_capacity` for each
terminal block. Keeping this arithmetic in one shared helper prevents the OME,
Scheduler, Operator, and template renderer from silently disagreeing about how
many ground interfaces a station physically has.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
)


class GroundTerminalTypeLike(Protocol):
    type: str


class GroundTerminalCapacityLike(GroundTerminalTypeLike, Protocol):
    count: int
    tracking_capacity: int


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


def station_ground_terminal_type(
    gs_file: GroundStationFile,
    station: GroundStationConfig,
) -> str:
    """Return the effective terminal type for a ground station."""
    terminals = station.terminals or gs_file.default_terminals
    return ground_terminal_type(terminals)
