# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Every body the production runtime supports has every fact the runtime needs: a shipped
catalog object, ephemeris data and orientation, runtime constants, a name in the body
vocabulary, and a largest separation from every other supported body. The page's body tables
are keyed by the same vocabulary, so the type check fails there for a body without its page
facts."""

from __future__ import annotations

from itertools import combinations
from typing import get_args

import pytest
import yaml
from nodalarc.body_frames import BODY_RUNTIME_SUPPORT, SupportedSurfaceBody
from nodalarc.models.catalog import Body
from nodalarc.physics.ephemeris_table import BODIES, BODY_SEPARATION_KM, FILES
from nodalarc.runtime_support import RuntimeSupport

from tests.catalog_session_fixtures import SHIPPED_ROOT

PRODUCTION = RuntimeSupport.production()
SUPPORTED = sorted(
    PRODUCTION.supported_central_bodies
    | PRODUCTION.supported_reference_bodies
    | PRODUCTION.supported_frame_bodies
)


@pytest.mark.parametrize("body", SUPPORTED)
def test_a_supported_body_has_every_runtime_fact(body: str) -> None:
    assert body in get_args(SupportedSurfaceBody.__value__), "missing from the body vocabulary"
    assert body in BODY_RUNTIME_SUPPORT, "no runtime constants"
    data = BODIES.get(body)
    assert data is not None, "no ephemeris data row"
    assert all(file_id in FILES for file_id in data.file_ids), "a data file is not in the table"
    shipped = SHIPPED_ROOT / "bodies" / f"{body}.yaml"
    assert shipped.is_file(), "no shipped catalog body"
    Body.model_validate(yaml.safe_load(shipped.read_text())["body"])


@pytest.mark.parametrize(("a", "b"), list(combinations(SUPPORTED, 2)))
def test_every_pair_of_supported_bodies_has_a_largest_separation(a: str, b: str) -> None:
    separation = BODY_SEPARATION_KM.get(frozenset({a, b}))
    assert separation is not None
    assert 0 < separation.nearest_km < separation.farthest_km
