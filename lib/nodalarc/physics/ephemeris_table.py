# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
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

type DataKind = Literal["spk", "bpc", "fk", "tpc", "gm"]


@dataclass(frozen=True, slots=True)
class EphemerisFile:
    """One NAIF data file shipped in ``configs/ephemerides/``."""

    file_id: str
    filename: str
    sha256: str
    # spk: positions; bpc: binary orientation; fk: frame definitions; tpc:
    # text planetary constants (IAU rotation models); gm: gravitational
    # parameters. ANISE reads fk and tpc only after a build-time conversion
    # (see datasets.py); the gm file is an input of the tpc conversion only.
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
    # file's coverage. None for a frame from an IAU rotation model, which
    # holds at every epoch: only the body's positions bound its coverage.
    coverage_orientation_id: int | None
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
            file_id="mars-satellites",
            filename="mar099s.bsp",
            sha256="997dc93ba640e476da7a494d2237dcdeb145e528db37be8ccee588c615e4e1ff",
            kind="spk",
            source_url=(
                "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/mar099s.bsp"
            ),
        ),
        EphemerisFile(
            file_id="planetary-constants",
            filename="pck00011.tpc",
            sha256="3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1",
            kind="tpc",
            source_url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
        ),
        EphemerisFile(
            file_id="planetary-gravity",
            filename="gm_de440.tpc",
            sha256="924ddf4fb9ead9fe8a1aa55780bcabde40b09d00065d58226e24b68d8092f140",
            kind="gm",
            source_url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/gm_de440.tpc",
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
#
# Mars's body-fixed frame is IAU_MARS (id 499) from the IAU rotation model in
# NAIF's planetary constants (pck00011); it matched SPICE within 0.0012
# millidegrees. de440s carries only the Mars system barycenter (id 4); the
# planet's center (id 499) comes from NAIF's Mars satellite ephemeris, whose
# short version covers 1995 to 2050 and so bounds a Mars session's dates. That
# file also carries Earth, the Earth-Moon barycenter and the Sun, as de440s
# does, and pck00011 also defines IAU rotation frames for Earth and the Moon.
# Neither changes Earth or Luna: Luna and Sun states and Earth and Luna
# orientation with and without Mars's files were identical, bit for bit, at
# 4,000 instants from 2001 to 2049.
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
        BodyData(
            body_id="mars",
            naif_id=499,
            fixed_orientation_id=499,
            coverage_orientation_id=None,
            file_ids=("de440s", "mars-satellites", "planetary-constants"),
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
SUN_ID = "sun"
# The solar disk obstructs link paths (solar conjunction). The Sun is not a catalog body, so its
# radius is a platform constant like its NAIF ID: the IAU 2015 nominal solar radius
# (Resolution B3), 695,700 km.
SUN_RADIUS_KM = 695_700.0


@dataclass(frozen=True, slots=True)
class BodySeparation:
    """The smallest and largest distance between two bodies' centers in the shipped data."""

    nearest_km: float
    farthest_km: float


# The distance between two supported bodies' centers, per pair, over the whole coverage of the
# shipped data, which bounds every sim time a session can reach (seeks included). The farthest
# distance bounds how far ahead or behind a signal between the two bodies queries the data; both
# bound the delay of every link between them. Nearest distances are recorded rounded down to the
# kilometre and farthest ones rounded up.
#
# Earth-Moon in de440s (1849 to 2150), from a 3 h scan of the whole coverage refined around each
# extremum: nearest 356,375.394 km (1912-01-04), farthest 406,719.705 km (2125-02-03).
# Pairs with Mars, over the Mars satellite ephemeris's coverage (1995 to 2050), from a 1 day
# (Earth) and 3 h (Luna) scan with each of the twelve most extreme local extrema refined to 1 s
# and 0.1 s: Earth-Mars nearest 55,758,005.714 km (2003-08-27), farthest 400,223,838.066 km
# (2019-08-28); Luna-Mars nearest 56,124,307.277 km (2003-08-24), farthest 400,283,850.226 km
# (2034-08-25).
BODY_SEPARATION_KM: dict[frozenset[str], BodySeparation] = {
    frozenset({"earth", "luna"}): BodySeparation(nearest_km=356_375.0, farthest_km=406_720.0),
    frozenset({"earth", "mars"}): BodySeparation(
        nearest_km=55_758_005.0, farthest_km=400_223_839.0
    ),
    frozenset({"luna", "mars"}): BodySeparation(nearest_km=56_124_307.0, farthest_km=400_283_851.0),
}


def body_separation(a: str, b: str) -> BodySeparation:
    """The recorded separation of two different bodies; refused when none is recorded."""
    separation = BODY_SEPARATION_KM.get(frozenset({a, b}))
    if a == b or separation is None:
        raise PhysicsDataError(f"no separation is recorded for bodies {a!r} and {b!r}")
    return separation


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
