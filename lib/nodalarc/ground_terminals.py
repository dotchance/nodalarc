# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Ground terminal capacity helpers.

Ground station terminal definitions describe groups of identical terminals.
The emulation capacity is therefore `count * tracking_capacity` for each
terminal block. Keeping this arithmetic in one shared helper prevents the OME,
Scheduler, Operator, and template renderer from silently disagreeing about how
many ground interfaces a station physically has.
"""

from __future__ import annotations

from collections.abc import Iterable

from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
)


def ground_terminal_capacity(terminals: Iterable[GroundTerminalDef]) -> int:
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
