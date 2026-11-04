"""Fixtures for the shared physics module: converted datasets and a SPICE reference.

Reference values come from SPICE loading the same NAIF files the platform
ships, in the same load order, so a difference is a difference in computation
and never a difference in data.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import spiceypy
from nodalarc.physics import EphemerisLocation, PhysicsContext
from nodalarc.physics.datasets import convert_datasets
from nodalarc.physics.ephemeris_table import BODIES, FILES, select_file_ids

EPHEMERIS_DIR = Path(__file__).resolve().parents[3] / "configs" / "ephemerides"


@pytest.fixture(scope="session")
def physics_location(tmp_path_factory: pytest.TempPathFactory) -> EphemerisLocation:
    location = EphemerisLocation(
        source_dir=EPHEMERIS_DIR, converted_dir=tmp_path_factory.mktemp("anise-datasets")
    )
    convert_datasets(location)
    return location


@pytest.fixture(scope="session")
def physics_context(physics_location: EphemerisLocation) -> PhysicsContext:
    return PhysicsContext(["earth", "luna"], location=physics_location)


@pytest.fixture(scope="session")
def spice_reference() -> Iterator[None]:
    """SPICE with every table body's files loaded in the table's load order.

    SPICE keeps one process-wide kernel pool; the pool is cleared afterwards.
    """
    spiceypy.kclear()
    for file_id in select_file_ids(BODIES):
        spiceypy.furnsh(str(EPHEMERIS_DIR / FILES[file_id].filename))
    yield
    spiceypy.kclear()


@pytest.fixture(scope="session")
def table_context(physics_location: EphemerisLocation) -> PhysicsContext:
    """A context with every body the ephemeris table carries."""
    return PhysicsContext(sorted(BODIES), location=physics_location)
