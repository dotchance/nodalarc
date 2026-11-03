# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Body positions, body orientation and orbit-element bases from ANISE.

One ``PhysicsContext`` per session (and per Builder preview) holds its own
ANISE almanac built from the files its bodies select, so answers for one
session cannot change because another session loaded other data.

Frame binding. NAIF's DE440 positions and its Earth and Moon orientation
files use ICRF axes under the label "J2000". ANISE treats its J2000
identifier as the root frame and applies the 23 milliarcsecond frame bias to
reach its ICRS, ICRF and GCRF identifiers: Earth's position relative to the
solar system barycenter through ANISE's ``ICRF`` was 13.371 km from SPICE,
while through ``SSB_J2000`` it matched exactly. NodalArc's ICRF axes are
therefore ANISE's J2000-labelled frames, and this module never uses ANISE's
ICRF, ICRS or GCRF identifiers. No other module names an ANISE frame.

Time. Master sim time is Unix seconds (UTC without leap seconds). It enters
ANISE through one function, ``epoch_of``; ANISE converts to TDB internally.
ANISE's TDB and SPICE's ephemeris time differ by about 9 microseconds, which
moves Earth's barycentric position by 0.28 m, inside the 1 m tolerance for
body positions.

Coverage. ANISE refuses queries outside its data, but with misleading text
("ID 3000 not in look up table") and with messages on stderr. This module
reads each body's data coverage from the loaded files at construction and
refuses an out-of-coverage time itself, with the body and the window named.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from anise import Almanac
from anise.astro import Frame
from anise.constants import Orientations
from anise.time import Epoch

from nodalarc.frames import Vec3
from nodalarc.physics.datasets import (
    EphemerisLocation,
    converted_path,
    production_location,
    verified_path,
)
from nodalarc.physics.ephemeris_table import (
    FILES,
    ORIGIN_NAIF_ID,
    body_data,
    select_file_ids,
)
from nodalarc.physics.errors import PhysicsDataError


def epoch_of(master_sim_time_unix: float) -> Epoch:
    """The single conversion from master sim time to an ANISE epoch."""
    return Epoch.init_from_unix_seconds(float(master_sim_time_unix))


@dataclass(frozen=True, slots=True)
class SelectedFile:
    """A data file a context loaded, as the resolved session records it."""

    file_id: str
    filename: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BodyState:
    """A body's center in the common frame: relative to Earth's center, ICRF axes."""

    body_id: str
    position_km: Vec3
    velocity_km_s: Vec3


@dataclass(frozen=True, slots=True)
class BodyOrientation:
    """Rotation from ICRF axes to a body-fixed frame, and its time derivative.

    ``rotation @ v_icrf`` gives body-fixed coordinates. A body-fixed velocity
    is ``rotation @ v + rotation_rate @ r``; the derivative comes from the
    orientation data, so no rotation rate constant is involved.
    """

    rotation: np.ndarray
    rotation_rate: np.ndarray


@dataclass(frozen=True, slots=True)
class _Coverage:
    start_unix: float
    end_unix: float


def _inertial_frame(naif_id: int) -> Frame:
    return Frame(naif_id, Orientations.J2000)


class PhysicsContext:
    """Body physics for one session's bodies."""

    def __init__(
        self,
        body_ids: Iterable[str],
        *,
        location: EphemerisLocation | None = None,
    ) -> None:
        self._location = location if location is not None else production_location()
        self._body_ids = tuple(sorted(set(body_ids)))
        selected: list[SelectedFile] = []
        almanac: Almanac | None = None
        for file_id in select_file_ids(self._body_ids):
            entry = FILES[file_id]
            if entry.kind == "fk":
                path = converted_path(self._location, entry)
            else:
                path = verified_path(
                    self._location.source_dir, entry.filename, entry.sha256, label=file_id
                )
            almanac = Almanac(str(path)) if almanac is None else almanac.load(str(path))
            selected.append(SelectedFile(file_id, entry.filename, entry.sha256))
        if almanac is None:
            raise PhysicsDataError("no ephemeris files selected")
        self._almanac = almanac
        self._selected = tuple(selected)
        self._coverage = {body: self._read_coverage(body) for body in self._body_ids}
        self._bases = {body: self._orbit_basis(body) for body in self._body_ids}

    @property
    def selected_files(self) -> tuple[SelectedFile, ...]:
        return self._selected

    @property
    def body_ids(self) -> tuple[str, ...]:
        return self._body_ids

    def _read_coverage(self, body_id: str) -> _Coverage:
        data = body_data(body_id)
        windows = [
            self._almanac.spk_domain(data.naif_id),
            self._almanac.spk_domain(ORIGIN_NAIF_ID),
            self._almanac.bpc_domain(data.coverage_orientation_id),
        ]
        start = max(w[0].to_unix_seconds() for w in windows)
        end = min(w[1].to_unix_seconds() for w in windows)
        return _Coverage(start_unix=start, end_unix=end)

    def require_coverage(self, body_id: str, master_sim_time_unix: float) -> None:
        coverage = self._coverage.get(body_id)
        if coverage is None:
            raise PhysicsDataError(
                f"body {body_id!r} is not in this session's physics context {self._body_ids}"
            )
        if not (coverage.start_unix <= master_sim_time_unix <= coverage.end_unix):
            raise PhysicsDataError(
                f"ephemeris data for {body_id!r} does not cover {epoch_of(master_sim_time_unix)}; "
                f"coverage is {epoch_of(coverage.start_unix)} to {epoch_of(coverage.end_unix)}"
            )

    def body_state(self, body_id: str, master_sim_time_unix: float) -> BodyState:
        """The body's center in the common frame (origin at Earth's center, ICRF axes)."""
        self.require_coverage(body_id, master_sim_time_unix)
        if body_id == "earth":
            zero = Vec3(0.0, 0.0, 0.0)
            return BodyState(body_id="earth", position_km=zero, velocity_km_s=zero)
        state = self._almanac.translate(
            _inertial_frame(body_data(body_id).naif_id),
            _inertial_frame(ORIGIN_NAIF_ID),
            epoch_of(master_sim_time_unix),
            None,
        )
        return BodyState(
            body_id=body_id,
            position_km=Vec3(state.x_km, state.y_km, state.z_km),
            velocity_km_s=Vec3(state.vx_km_s, state.vy_km_s, state.vz_km_s),
        )

    def body_states(
        self, body_ids: Iterable[str], master_sim_time_unix: float
    ) -> dict[str, BodyState]:
        return {body: self.body_state(body, master_sim_time_unix) for body in sorted(set(body_ids))}

    def orientation(self, body_id: str, master_sim_time_unix: float) -> BodyOrientation:
        """Rotation from ICRF axes to the body-fixed frame, with its derivative."""
        self.require_coverage(body_id, master_sim_time_unix)
        data = body_data(body_id)
        dcm = self._almanac.rotate(
            _inertial_frame(data.naif_id),
            Frame(data.naif_id, data.fixed_orientation_id),
            epoch_of(master_sim_time_unix),
        )
        if dcm.rot_mat_dt is None:
            raise PhysicsDataError(f"orientation data for {body_id!r} carries no rotation rate")
        return BodyOrientation(
            rotation=np.asarray(dcm.rot_mat, dtype=np.float64),
            rotation_rate=np.asarray(dcm.rot_mat_dt, dtype=np.float64),
        )

    def orbit_basis(self, body_id: str) -> np.ndarray:
        """Rows are the axes of the body's orbit-element frame on ICRF axes.

        ``basis.T @ r_elements`` gives ICRF coordinates.
        """
        try:
            return self._bases[body_id]
        except KeyError as exc:
            raise PhysicsDataError(
                f"body {body_id!r} is not in this session's physics context {self._body_ids}"
            ) from exc

    def _orbit_basis(self, body_id: str) -> np.ndarray:
        # Earth orbits keep their meaning: elements are measured on ICRF
        # axes. Every other body's elements are measured from its own
        # equator, fixed at J2000: z is the body's pole at J2000, x is the
        # ascending node of that equator on the ICRF equator, y completes
        # the set. Measuring lunar orbits from Earth's equator, as NodalArc
        # did before, put the shipped "polar" lunar orbit about 66 degrees
        # from the Moon's equator. The construction matched the same
        # construction done in SPICE exactly, and differs
        # from SPICE's MARSIAU by 1.67 arcseconds, because MARSIAU uses
        # older Mars rotation constants.
        if body_id == "earth":
            return np.eye(3)
        data = body_data(body_id)
        j2000 = Epoch.init_from_et_seconds(0.0)
        dcm = self._almanac.rotate(
            _inertial_frame(data.naif_id), Frame(data.naif_id, data.fixed_orientation_id), j2000
        )
        pole = np.asarray(dcm.rot_mat, dtype=np.float64)[2, :]
        pole = pole / np.linalg.norm(pole)
        x_axis = np.cross([0.0, 0.0, 1.0], pole)
        x_axis /= np.linalg.norm(x_axis)
        y_axis = np.cross(pole, x_axis)
        return np.vstack([x_axis, y_axis, pole])
