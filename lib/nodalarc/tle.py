# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""TLE parsing helpers shared by loaders, validators, and propagators."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def tle_norad_id(line_1: str) -> int:
    """Parse the NORAD catalog ID from a TLE line 1."""
    if not line_1.startswith("1 "):
        raise ValueError(f"TLE line 1 must start with '1 ': {line_1!r}")
    try:
        return int(line_1[2:7])
    except ValueError as exc:
        raise ValueError(f"Invalid TLE line 1 NORAD id field: {line_1!r}") from exc


def tle_line_2_norad_id(line_2: str) -> int:
    """Parse the NORAD catalog ID from a TLE line 2."""
    if not line_2.startswith("2 "):
        raise ValueError(f"TLE line 2 must start with '2 ': {line_2!r}")
    try:
        return int(line_2[2:7])
    except ValueError as exc:
        raise ValueError(f"Invalid TLE line 2 NORAD id field: {line_2!r}") from exc


def validate_tle_pair(line_1: str, line_2: str) -> None:
    """Validate that two TLE lines form one catalog record."""
    norad_1 = tle_norad_id(line_1)
    norad_2 = tle_line_2_norad_id(line_2)
    if norad_1 != norad_2:
        raise ValueError(f"TLE line number mismatch: {line_1!r} / {line_2!r}")


def tle_epoch_unix(line_1: str) -> float:
    """Parse the TLE epoch field into a UTC Unix timestamp.

    The two-digit epoch year follows the NORAD convention: years 57-99 map to
    1957-1999 and years 00-56 map to 2000-2056. This keeps freshness checks
    independent from any propagator implementation.
    """
    if not line_1.startswith("1 "):
        raise ValueError(f"TLE line 1 must start with '1 ': {line_1!r}")
    try:
        epoch_year = int(line_1[18:20])
        epoch_day = float(line_1[20:32])
    except ValueError as exc:
        raise ValueError(f"Invalid TLE epoch field: {line_1!r}") from exc

    year = 1900 + epoch_year if epoch_year >= 57 else 2000 + epoch_year
    epoch = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=epoch_day - 1.0)
    return epoch.timestamp()


def tle_age_days(line_1: str, sim_epoch_unix: float) -> float:
    """Return absolute age in days between a TLE epoch and simulation epoch."""
    return abs(sim_epoch_unix - tle_epoch_unix(line_1)) / 86400.0
