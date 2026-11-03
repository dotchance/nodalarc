# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The platform's ephemeris table: which data files describe each body.

Where a body is and how it is oriented is platform physics, like the J2
constant and the propagation code, so the session does not choose these files.
A session's bodies select rows of this table, and the resolved session records
the selected files and their checksums so every result can be traced to its
data. Changing the ephemeris means editing this table and the files in
``configs/ephemerides/``, then rebuilding the images.

Each file is pinned by SHA-256. A file that does not match its pin is refused;
the platform never runs on data it cannot identify.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from nodalarc.physics.errors import PhysicsDataError

type DataKind = Literal["spk", "bpc", "fk"]


@dataclass(frozen=True, slots=True)
class EphemerisFile:
    """One NAIF data file shipped in ``configs/ephemerides/``."""

    file_id: str
    filename: str
    sha256: str
    # spk: positions; bpc: binary orientation; fk: frame definitions, which
    # ANISE reads only after a build-time conversion (see datasets.py).
    kind: DataKind
    source_url: str


@dataclass(frozen=True, slots=True)
class BodyData:
    """The data rows one body needs, in load order.

    Load order is precedence: where two orientation files cover the same
    instant, the file loaded later wins, in ANISE as in SPICE. Earth's
    high-precision orientation file is loaded after its long-term predict
    file, so measured Earth orientation wins wherever it exists.
    """

    body_id: str
    naif_id: int
    # ANISE orientation id of the body-fixed frame used for surface sites
    # and body-fixed satellite states.
    fixed_orientation_id: int
    # ANISE orientation id whose loaded data bound the coverage of that
    # body-fixed frame. For Luna the mean Earth/polar axis frame is a fixed
    # rotation of the principal-axes data, so coverage is the principal-axes
    # file's coverage.
    coverage_orientation_id: int
    file_ids: tuple[str, ...]


FILES: dict[str, EphemerisFile] = {
    f.file_id: f
    for f in (
        EphemerisFile(
            file_id="de440s",
            filename="de440s.bsp",
            sha256="c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2",
            kind="spk",
            source_url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp",
        ),
        EphemerisFile(
            file_id="earth-orientation-predict",
            filename="earth_2026_260806_2126_predict.bpc",
            sha256="9f565ab5aaccd33afe54f3a1b20cb8b136636c31a0bd4c0986fac722b931c280",
            kind="bpc",
            source_url=(
                "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/"
                "earth_2026_260806_2126_predict.bpc"
            ),
        ),
        EphemerisFile(
            file_id="earth-orientation-measured",
            filename="earth_latest_high_prec.bpc",
            sha256="c329bed712083e0ae7b6ff737b9552fd80b6e111d4a2a7b97394e98fe5df8b7d",
            kind="bpc",
            source_url=(
                "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/earth_latest_high_prec.bpc"
            ),
        ),
        EphemerisFile(
            file_id="moon-orientation",
            filename="moon_pa_de440_200625.bpc",
            sha256="60cd55aa401ea2ea97360636f567554bfe4e37bb829f901b4460a455dfaf783f",
            kind="bpc",
            source_url=(
                "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc"
            ),
        ),
        EphemerisFile(
            file_id="moon-frames",
            filename="moon_de440_250416.tf",
            sha256="a47c71e9c9f33796bdafb2c9d69a7ee447b6016ecad80f71cd6f3e479f9cf768",
            kind="fk",
            source_url=(
                "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/"
                "moon_de440_250416.tf"
            ),
        ),
    )
}

# ITRF93 (ANISE orientation id 3000) is Earth's body-fixed frame. NAIF's Earth
# orientation files carry measured and predicted Earth rotation and polar
# motion; ANISE's ITRF93 matched SPICE within 0.0012 millidegrees, while
# the GMST formula NodalArc used before ignores precession, nutation and polar
# motion.
#
# Luna's surface frame is the mean Earth/polar axis frame (id 31009,
# MOON_ME_DE440_ME421), the frame lunar maps and site coordinates use. Its
# orientation comes from the DE440 principal-axes file (id 31008) through the
# fixed rotation in NAIF's lunar frame kernel. The principal-axes frame is a
# dynamical frame 0.029 degrees from the map frame, which moves a surface
# point by about 875 m.
BODIES: dict[str, BodyData] = {
    b.body_id: b
    for b in (
        BodyData(
            body_id="earth",
            naif_id=399,
            fixed_orientation_id=3000,
            coverage_orientation_id=3000,
            file_ids=("de440s", "earth-orientation-predict", "earth-orientation-measured"),
        ),
        BodyData(
            body_id="luna",
            naif_id=301,
            fixed_orientation_id=31009,
            coverage_orientation_id=31008,
            file_ids=("de440s", "moon-orientation", "moon-frames"),
        ),
    )
}


def body_data(body_id: str) -> BodyData:
    try:
        return BODIES[body_id]
    except KeyError as exc:
        raise PhysicsDataError(
            f"no ephemeris data for body {body_id!r}; bodies with data: {sorted(BODIES)}"
        ) from exc


# The common frame NodalArc publishes in is centered on Earth's center with
# ICRF axes. That center is a coordinate origin, read from the planetary
# positions file, which carries every planet; it does not make Earth part of
# a session. A Mars and Venus session loads no Earth orientation data.
ORIGIN_FILE_ID = "de440s"
ORIGIN_NAIF_ID = 399

# The Sun's position lights the page's bodies. It comes from the same
# planetary positions file, which every context loads, so lighting needs no
# extra file and does not make the Sun a session body.
SUN_NAIF_ID = 10


def select_file_ids(body_ids: Iterable[str]) -> tuple[str, ...]:
    """The files the session's bodies need, in load order, each once."""
    bodies = sorted(set(body_ids))
    if not bodies:
        raise PhysicsDataError("a physics context needs at least one body")
    ordered: list[str] = [ORIGIN_FILE_ID]
    for body_id in bodies:
        for file_id in body_data(body_id).file_ids:
            if file_id not in ordered:
                ordered.append(file_id)
    return tuple(ordered)
